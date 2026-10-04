"""Sentinel-2 L2A / WorldCover access through the Microsoft Planetary Computer STAC API.

No login needed: asset URLs are signed anonymously with ``planetary_computer.sign`` right
before each read (tokens expire, so we never store signed URLs).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

import numpy as np
from shapely.geometry import shape
from shapely.geometry.base import BaseGeometry

from .geometry import Grid, reproject_geom

GDAL_ENV = {
    "GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
    "GDAL_HTTP_MAX_RETRY": "5",
    "GDAL_HTTP_RETRY_DELAY": "2",
    "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif,.tiff",
    "VSI_CACHE": "TRUE",
}


@dataclass(frozen=True)
class SceneRef:
    """The subset of a STAC item the pipeline needs (plain data, easy to test)."""

    item_id: str
    acquired_at: datetime
    mgrs_tile: str | None
    epsg: int | None
    cloud_cover: float | None
    processing_baseline: str | None
    generation: str  # processing timestamp; later = reprocessed version
    footprint: BaseGeometry | None
    hrefs: dict[str, str]

    @property
    def obs_date(self) -> date:
        return self.acquired_at.date()


def _parse_epsg(props: dict[str, Any]) -> int | None:
    if props.get("proj:epsg"):
        return int(props["proj:epsg"])
    code = props.get("proj:code")
    if isinstance(code, str) and code.upper().startswith("EPSG:"):
        return int(code.split(":", 1)[1])
    return None


def scene_from_item(item: Any) -> SceneRef:
    props = item.properties
    acquired = item.datetime or datetime.fromisoformat(props["datetime"].replace("Z", "+00:00"))
    generation = str(props.get("s2:generation_time") or item.id.rsplit("_", 1)[-1])
    return SceneRef(
        item_id=item.id,
        acquired_at=acquired,
        mgrs_tile=props.get("s2:mgrs_tile"),
        epsg=_parse_epsg(props),
        cloud_cover=props.get("eo:cloud_cover"),
        processing_baseline=props.get("s2:processing_baseline"),
        generation=generation,
        footprint=shape(item.geometry) if item.geometry else None,
        hrefs={k: item.assets[k].href for k in ("B04", "B08", "SCL") if k in item.assets},
    )


def select_scenes(
    scenes: Iterable[SceneRef], window_lonlat: BaseGeometry, preferred_epsg: int
) -> list[SceneRef]:
    """Keep one scene per acquisition date.

    Planetary Computer often lists the same acquisition several times: reprocessed versions
    (same datatake/tile, newer generation time) and overlapping MGRS tiles (e.g. 48QTD and
    47QRU near 102°E). Preference order: footprint fully contains the read window, tile in
    the plot's own UTM zone, newest processing, lowest scene cloud.
    """
    best: dict[date, tuple[tuple, SceneRef]] = {}
    for s in scenes:
        if not {"B04", "B08", "SCL"} <= set(s.hrefs):
            continue
        contains = bool(s.footprint is not None and s.footprint.contains(window_lonlat))
        key = (
            contains,
            s.epsg == preferred_epsg,
            s.generation,
            -(s.cloud_cover if s.cloud_cover is not None else 100.0),
        )
        d = s.obs_date
        if d not in best or key > best[d][0]:
            best[d] = (key, s)
    return [best[d][1] for d in sorted(best)]


class StacClient:
    def __init__(self, url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"):
        import pystac_client

        self._client = pystac_client.Client.open(url)

    def search(
        self,
        collection: str,
        geom_lonlat: BaseGeometry,
        start: date | str,
        end: date | str,
        max_cloud: float | None = None,
    ) -> list[Any]:
        query = {"eo:cloud_cover": {"lt": max_cloud}} if max_cloud is not None else None
        search = self._client.search(
            collections=[collection],
            intersects=geom_lonlat.__geo_interface__,
            datetime=f"{start}/{end}",
            query=query,
        )
        return list(search.items())

    def sentinel2(
        self, geom_lonlat: BaseGeometry, start: date | str, end: date | str, max_cloud: float
    ) -> list[SceneRef]:
        return [
            scene_from_item(i)
            for i in self.search("sentinel-2-l2a", geom_lonlat, start, end, max_cloud)
        ]

    def worldcover_href(self, geom_lonlat: BaseGeometry, year: str = "2021") -> str:
        items = self._client.search(
            collections=["esa-worldcover"], intersects=geom_lonlat.__geo_interface__
        ).items()
        for it in items:
            if year in it.id:
                return it.assets["map"].href
        raise LookupError(f"no ESA WorldCover {year} tile covers the plot")


def sign(href: str) -> str:
    import planetary_computer

    return planetary_computer.sign(href)


def read_window(href: str, grid: Grid, resampling: str = "nearest") -> np.ndarray:
    """Read ``grid`` bounds from a COG in the *same CRS* (S2 tile), resampled to grid shape.

    Grid bounds are snapped to the 20 m S2 grid, so 10 m bands read 1:1 and the 20 m SCL is
    upsampled exactly 2x with nearest neighbour.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.windows import from_bounds

    with rasterio.Env(**GDAL_ENV), rasterio.open(sign(href)) as src:
        if src.crs.to_epsg() != grid.epsg:
            raise ValueError(f"asset CRS {src.crs} != grid EPSG:{grid.epsg}")
        win = from_bounds(*grid.bounds, transform=src.transform)
        return src.read(
            1,
            window=win,
            out_shape=grid.shape,
            resampling=getattr(Resampling, resampling),
            boundless=True,
            fill_value=0,
        )


def read_reprojected(href: str, grid: Grid) -> np.ndarray:
    """Read a raster in any CRS (e.g. WorldCover, EPSG:4326) onto ``grid`` (nearest)."""
    import rasterio
    from rasterio.warp import Resampling, reproject, transform_bounds
    from rasterio.windows import from_bounds

    with rasterio.Env(**GDAL_ENV), rasterio.open(sign(href)) as src:
        gb = transform_bounds(f"EPSG:{grid.epsg}", src.crs, *grid.bounds, densify_pts=21)
        win = from_bounds(*gb, transform=src.transform).round_offsets().round_lengths()
        arr = src.read(1, window=win, boundless=True, fill_value=0)
        out = np.zeros(grid.shape, dtype=arr.dtype)
        reproject(
            arr,
            out,
            src_transform=src.window_transform(win),
            src_crs=src.crs,
            dst_transform=grid.transform,
            dst_crs=f"EPSG:{grid.epsg}",
            resampling=Resampling.nearest,
        )
        return out


def grid_footprint_lonlat(grid: Grid) -> BaseGeometry:
    from shapely.geometry import box

    return reproject_geom(box(*grid.bounds), grid.epsg, 4326)
