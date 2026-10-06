import numpy as np, pandas as pd, rasterio, pystac_client, planetary_computer as pc, json, sys
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.windows import from_bounds
from pyproj import Transformer
from concurrent.futures import ThreadPoolExecutor
import os
os.environ.setdefault('GDAL_HTTP_MAX_RETRY','4'); os.environ.setdefault('GDAL_HTTP_RETRY_DELAY','2')
os.environ['GDAL_DISABLE_READDIR_ON_OPEN']='EMPTY_DIR'
cat=pystac_client.Client.open('https://planetarycomputer.microsoft.com/api/stac/v1',modifier=pc.sign_inplace)
# 1 km boxes, UTM 48N (EPSG:32648) centers
AREAS={
 'NongRuea':  dict(cx=226500,cy=1832500,tile='48QTD'),
 'ManchaKhiri':dict(cx=231500,cy=1781500,tile='48PTC'),
 'NamPhong':  dict(cx=273500,cy=1850500,tile='48QTD'),
 'Kranuan':   dict(cx=289500,cy=1844500,tile='48QTD'),
}
CRS='EPSG:32648'; T=Transformer.from_crs(CRS,4326,always_xy=True)
for a in AREAS.values():
    a['bounds']=(a['cx']-500,a['cy']-500,a['cx']+500,a['cy']+500)
    a['lon'],a['lat']=T.transform(a['cx'],a['cy'])
    a['bbox_ll']=transform_bounds(CRS,4326,*a['bounds'])

def read_box(href,b,scale=1):
    with rasterio.open(href) as src:
        assert str(src.crs)==CRS, src.crs
        return src.read(1,window=from_bounds(*b,src.transform))

def masks(name,a):
    lon,lat=a['lon'],a['lat']
    it=[i for i in cat.search(collections=['sentinel-2-l2a'],intersects={'type':'Point','coordinates':[lon,lat]},datetime='2025-12-01/2025-12-01').items() if i.properties['s2:mgrs_tile']==a['tile']][0]
    r=read_box(it.assets['B04'].href,a['bounds']).astype(float)-1000
    n=read_box(it.assets['B08'].href,a['bounds']).astype(float)-1000
    ndvi=(n-r)/(n+r)
    wc=[i for i in cat.search(collections=['esa-worldcover'],intersects={'type':'Point','coordinates':[lon,lat]}).items() if '2021' in i.id][0]
    with rasterio.open(wc.assets['map'].href) as src:
        gb=transform_bounds(CRS,src.crs,*a['bounds'],densify_pts=21)
        ww=from_bounds(*gb,src.transform).round_offsets().round_lengths()
        arr=src.read(1,window=ww); wtr=src.window_transform(ww)
    lc=np.zeros((100,100),np.uint8)
    dtr=rasterio.transform.from_origin(a['bounds'][0],a['bounds'][3],10,10)
    reproject(arr,lc,src_transform=wtr,src_crs=src.crs,dst_transform=dtr,dst_crs=CRS,resampling=Resampling.nearest)
    crop=lc==40
    vals,cnts=np.unique(lc,return_counts=True)
    a['worldcover_pct']={int(v):round(100*c/lc.size,1) for v,c in zip(vals,cnts)}
    a['m_crop']=crop; a['m_cane']=crop&(ndvi>0.6); a['m_other']=crop&(ndvi<0.4)
    a['frac_cane']=a['m_cane'].mean(); a['frac_other']=a['m_other'].mean()

for k,a in AREAS.items():
    masks(k,a); print(k,round(a['lat'],4),round(a['lon'],4),a['worldcover_pct'],'cane-like %.2f other %.2f'%(a['frac_cane'],a['frac_other']),flush=True)

DATES=sys.argv[1] if len(sys.argv)>1 else '2023-10-01/2026-10-04'
CLEAR={4,5}  # SCL vegetation, bare soil
def proc(args):
    k,it=args; a=AREAS[k]
    try:
        scl=read_box(it.assets['SCL'].href,a['bounds']); scl=np.repeat(np.repeat(scl,2,0),2,1)
        r=read_box(it.assets['B04'].href,a['bounds']).astype(float)
        n=read_box(it.assets['B08'].href,a['bounds']).astype(float)
    except Exception as e:
        return dict(area=k,item=it.id,error=str(e)[:200])
    off=1000 if float(it.properties.get('s2:processing_baseline','0'))>=4 else 0
    r-=off; n-=off
    with np.errstate(invalid='ignore',divide='ignore'): ndvi=(n-r)/(n+r)
    ok=np.isin(scl,list(CLEAR))&(r>0)&(n>0)&np.isfinite(ndvi)
    out=dict(area=k,item=it.id,datetime=it.properties['datetime'],tile=it.properties['s2:mgrs_tile'],
             scene_cloud=it.properties['eo:cloud_cover'],baseline=it.properties.get('s2:processing_baseline'))
    for m in ['crop','cane','other']:
        mm=a['m_'+m]; v=ok&mm
        out[f'valid_frac_{m}']=v.sum()/max(mm.sum(),1)
        out[f'ndvi_{m}']=float(np.nanmean(ndvi[v])) if v.sum()>0 else np.nan
    return out

jobs=[]
for k,a in AREAS.items():
    its=[i for i in cat.search(collections=['sentinel-2-l2a'],bbox=a['bbox_ll'],datetime=DATES,query={'s2:mgrs_tile':{'eq':a['tile']},'eo:cloud_cover':{'lt':95}}).items()]
    print(k,len(its),'items',flush=True)
    jobs+= [(k,i) for i in its]
with ThreadPoolExecutor(16) as ex:
    rows=[]
    for i,r in enumerate(ex.map(proc,jobs)):
        rows.append(r)
        if i%100==0: print(i,len(jobs),flush=True)
df=pd.DataFrame(rows); df.to_csv('raw_scenes.csv',index=False)
meta={k:dict(center_lat=a['lat'],center_lon=a['lon'],utm48n_center=(a['cx'],a['cy']),bbox_lonlat=a['bbox_ll'],tile=a['tile'],
       worldcover2021_pct=a['worldcover_pct'],frac_probable_cane_px=a['frac_cane'],frac_other_crop_px=a['frac_other']) for k,a in AREAS.items()}
json.dump(meta,open('areas.json','w'),indent=1)
print('errors',df.get('error',pd.Series()).notna().sum())
