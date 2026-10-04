import pytest
from shapely.geometry import Polygon

from canesat.geometry import (
    build_plot_grid,
    geom_from_geojson,
    reproject_geom,
    snapped_grid,
    square_around,
    utm_epsg_for,
)

NONG_RUEA = (16.5589, 102.4372)


def test_utm_zone():
    assert utm_epsg_for(102.4372, 16.5589) == 32648
    assert utm_epsg_for(101.9, 16.5) == 32647
    assert utm_epsg_for(-70, -10) == 32719


def test_square_around_is_1km2():
    sq = square_around(*NONG_RUEA, 1000)
    area = reproject_geom(sq, 4326, 32648).area
    assert area == pytest.approx(1e6, rel=1e-3)
    c = sq.centroid
    assert (c.y, c.x) == pytest.approx(NONG_RUEA, abs=1e-4)


def test_grid_snapped_to_20m():
    g = snapped_grid(Polygon([(226003, 1832007), (226511, 1832007), (226511, 1832499)]), 32648, 0)
    assert all(v % 20 == 0 for v in g.bounds)
    assert g.shape == ((g.bounds[3] - g.bounds[1]) / 10, (g.bounds[2] - g.bounds[0]) / 10)


def test_inner_buffer_removes_edge_pixels():
    sq = square_around(*NONG_RUEA, 1000)
    pg0 = build_plot_grid(sq, 32648, inner_buffer_m=0, ring_outer_m=500)
    pg = build_plot_grid(sq, 32648, inner_buffer_m=10, ring_outer_m=500)
    n0, n = int(pg0.plot_mask.sum()), int(pg.plot_mask.sum())
    assert 9800 < n0 < 10200  # ~100 x 100 px of 10 m
    assert n < n0 and n == pytest.approx(n0 * (980 / 1000) ** 2, rel=0.02)
    assert not pg.flags


def test_ring_excludes_plot_and_gap():
    sq = square_around(*NONG_RUEA, 1000)
    pg = build_plot_grid(sq, 32648, ring_gap_m=50, ring_outer_m=1000)
    assert not (pg.ring_mask & pg.plot_mask).any()
    assert pg.ring_mask.sum() > 4 * pg.plot_mask.sum()


def test_small_plot_falls_back_to_unbuffered():
    # 25 m x 25 m: an inward 10 m buffer leaves < 4 pixels
    x, y = 226500, 1832500
    tiny = reproject_geom(
        Polygon([(x, y), (x + 25, y), (x + 25, y + 25), (x, y + 25)]), 32648, 4326
    )
    pg = build_plot_grid(tiny, 32648, inner_buffer_m=10, min_px=4, ring_outer_m=200)
    assert "small_plot_no_buffer" in pg.flags
    assert pg.plot_mask.sum() >= 4


def test_geom_from_geojson_feature_collection():
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {},
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [
                        [[102.43, 16.55], [102.44, 16.55], [102.44, 16.56], [102.43, 16.55]]
                    ],
                },
            }
        ],
    }
    g = geom_from_geojson(fc)
    assert g.geom_type == "MultiPolygon" and g.is_valid
