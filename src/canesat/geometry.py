"""Plot geometry helpers: reprojection, inward buffering, pixel grids and masks."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from affine import Affine
from pyproj import CRS, Transformer
from rasterio.features import geometry_mask
from shapely import ops
from shapely.geometry import MultiPolygon, Polygon, box, mapping, shape
from shapely.geometry.base import BaseGeometry

S2_GRID_M = 20.0  # snap windows to the 20 m grid so 10 m and 20 m bands align exactly
PIXEL_M = 10.0


def utm_epsg_for(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180) / 6) + 1)
    return (32600 if lat >= 0 else 32700) + zone


def reproject_geom(geom: BaseGeometry, src_epsg: int, dst_epsg: int) -> BaseGeometry:
    if src_epsg == dst_epsg:
        return geom
    t = Transformer.from_crs(CRS.from_epsg(src_epsg), CRS.from_epsg(dst_epsg), always_xy=True)
    return ops.transform(t.transform, geom)


def as_multipolygon(geom: BaseGeometry) -> MultiPolygon:
    if isinstance(geom, MultiPolygon):
        return geom
    if isinstance(geom, Polygon):
        return MultiPolygon([geom])
    raise ValueError(f"plot geometry must be (Multi)Polygon, got {geom.geom_type}")


def geom_from_geojson(obj: dict[str, Any] | str) -> MultiPolygon:
    """Accept a GeoJSON geometry, Feature or FeatureCollection (first feature)."""
    if isinstance(obj, str):
        obj = json.loads(obj)
    if obj.get("type") == "FeatureCollection":
        obj = obj["features"][0]
    if obj.get("type") == "Feature":
        obj = obj["geometry"]
    geom = shape(obj)
    if not geom.is_valid:
        geom = geom.buffer(0)
    return as_multipolygon(geom)


def square_around(lat: float, lon: float, size_m: float = 1000.0) -> MultiPolygon:
    """Square of ``size_m`` (in local UTM metres) centred on lat/lon, returned in EPSG:4326."""
    epsg = utm_epsg_for(lon, lat)
    to_utm = Transformer.from_crs(4326, epsg, always_xy=True)
    x, y = to_utm.transform(lon, lat)
    h = size_m / 2
    sq = box(x - h, y - h, x + h, y + h)
    return as_multipolygon(reproject_geom(sq, epsg, 4326))


def inner_buffer(geom_m: BaseGeometry, distance_m: float) -> BaseGeometry:
    """Shrink a projected polygon by ``distance_m`` (empty if the plot is too thin)."""
    if distance_m <= 0:
        return geom_m
    return geom_m.buffer(-distance_m, join_style="mitre")


@dataclass(frozen=True)
class Grid:
    """A 10 m pixel grid (north-up) in a projected CRS, aligned to the S2 20 m grid."""

    epsg: int
    bounds: tuple[float, float, float, float]  # minx, miny, maxx, maxy (multiples of 20 m)
    res: float = PIXEL_M

    @property
    def width(self) -> int:
        return round((self.bounds[2] - self.bounds[0]) / self.res)

    @property
    def height(self) -> int:
        return round((self.bounds[3] - self.bounds[1]) / self.res)

    @property
    def shape(self) -> tuple[int, int]:
        return (self.height, self.width)

    @property
    def transform(self) -> Affine:
        return Affine(self.res, 0.0, self.bounds[0], 0.0, -self.res, self.bounds[3])

    def rasterize(self, geom: BaseGeometry) -> np.ndarray:
        """Boolean mask of pixels whose centre falls inside ``geom``."""
        if geom.is_empty:
            return np.zeros(self.shape, dtype=bool)
        return geometry_mask(
            [mapping(geom)], out_shape=self.shape, transform=self.transform, invert=True
        )


def snapped_grid(geom_m: BaseGeometry, epsg: int, margin_m: float) -> Grid:
    minx, miny, maxx, maxy = geom_m.bounds
    g = S2_GRID_M
    return Grid(
        epsg=epsg,
        bounds=(
            math.floor((minx - margin_m) / g) * g,
            math.floor((miny - margin_m) / g) * g,
            math.ceil((maxx + margin_m) / g) * g,
            math.ceil((maxy + margin_m) / g) * g,
        ),
    )


@dataclass
class PlotGrid:
    """Everything needed to compute per-scene statistics for one plot in one CRS."""

    grid: Grid
    plot_mask: np.ndarray  # pixels used for plot statistics (inner-buffered polygon)
    ring_mask: np.ndarray  # annulus around the plot for the cane-pixel neighbour baseline
    flags: list[str] = field(default_factory=list)


def build_plot_grid(
    geom_lonlat: BaseGeometry,
    epsg: int,
    inner_buffer_m: float = 10.0,
    min_px: int = 4,
    ring_gap_m: float = 50.0,
    ring_outer_m: float = 2500.0,
) -> PlotGrid:
    geom_m = reproject_geom(geom_lonlat, 4326, epsg)
    grid = snapped_grid(geom_m, epsg, margin_m=ring_outer_m + S2_GRID_M)
    flags: list[str] = []
    plot_mask = grid.rasterize(inner_buffer(geom_m, inner_buffer_m))
    if int(plot_mask.sum()) < min_px:
        # small/thin plot: fall back to the unbuffered polygon and flag it (design §5.1, §15)
        flags.append("small_plot_no_buffer")
        plot_mask = grid.rasterize(geom_m)
    ring = geom_m.buffer(ring_outer_m).difference(geom_m.buffer(ring_gap_m))
    ring_mask = grid.rasterize(ring)
    return PlotGrid(grid=grid, plot_mask=plot_mask, ring_mask=ring_mask, flags=flags)
