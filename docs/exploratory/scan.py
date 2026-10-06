import numpy as np, rasterio, pystac_client, planetary_computer as pc
from rasterio.warp import transform_bounds, reproject, Resampling
from rasterio.windows import from_bounds
from pyproj import Transformer
cat=pystac_client.Client.open('https://planetarycomputer.microsoft.com/api/stac/v1',modifier=pc.sign_inplace)
regions={'NamPhong':(102.95,16.70,'48QTD'),'NongRuea_PhuWiang':(102.45,16.50,'48QTD'),'ManchaKhiri':(102.55,16.15,'48PTC'),'Kranuan':(103.08,16.72,'48QTD')}
HALF=7000
for name,(lon,lat,tile) in regions.items():
    it=[i for i in cat.search(collections=['sentinel-2-l2a'],intersects={'type':'Point','coordinates':[lon,lat]},datetime='2025-12-01/2025-12-01').items() if i.properties['s2:mgrs_tile']==tile][0]
    with rasterio.open(it.assets['B04'].href) as src:
        crs=src.crs; x,y=Transformer.from_crs(4326,crs,always_xy=True).transform(lon,lat)
        x=round(x/1000)*1000; y=round(y/1000)*1000
        b=(x-HALF,y-HALF,x+HALF,y+HALF)
        w=from_bounds(*b,src.transform); r=src.read(1,window=w).astype(float)-1000; tr=src.window_transform(w)
    with rasterio.open(it.assets['B08'].href) as src: n=src.read(1,window=from_bounds(*b,src.transform)).astype(float)-1000
    ndvi=(n-r)/(n+r)
    wc=[i for i in cat.search(collections=['esa-worldcover'],intersects={'type':'Point','coordinates':[lon,lat]}).items() if '2021' in i.id][0]
    with rasterio.open(wc.assets['map'].href) as src:
        gb=transform_bounds(crs,src.crs,*b,densify_pts=21)
        ww=from_bounds(*gb,src.transform).round_offsets().round_lengths()
        arr=src.read(1,window=ww); wtr=src.window_transform(ww)
        lc=np.zeros(ndvi.shape,np.uint8)
        reproject(arr,lc,src_transform=wtr,src_crs=src.crs,dst_transform=tr,dst_crs=crs,resampling=Resampling.nearest)
    crop=lc==40; cane=crop&(ndvi>0.6)
    nb=ndvi.shape[0]//100; res=[]
    for i in range(nb):
        for j in range(nb):
            s=(slice(i*100,(i+1)*100),slice(j*100,(j+1)*100))
            res.append((cane[s].mean(),crop[s].mean(),np.nanmean(ndvi[s]),i,j))
    res.sort(reverse=True)
    print(name,tile,crs,'cropland frac whole window %.2f'%crop.mean(),'cane-like frac %.2f'%cane.mean())
    for c,cr,nv,i,j in res[:4]:
        cx=b[0]+j*1000+500; cy=b[3]-i*1000-500
        lo,la=Transformer.from_crs(crs,4326,always_xy=True).transform(cx,cy)
        print('  canelike=%.2f crop=%.2f ndviDec=%.2f center=(%.4f,%.4f) utm=(%d,%d)'%(c,cr,nv,la,lo,cx,cy))
