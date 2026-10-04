from datetime import UTC, datetime

import numpy as np
import pytest

from canesat.config import IngestConfig
from canesat.geometry import build_plot_grid, square_around
from canesat.ingest import PlotIngestor, compute_observation
from canesat.stac import SceneRef

NONG_RUEA = (16.5589, 102.4372)


def _scene(item_id="S2X_MSIL2A_20250901T033541_R061_T48QTD_20250901T080000", baseline="05.11"):
    return SceneRef(
        item_id=item_id,
        acquired_at=datetime(2025, 9, 1, 3, 35, tzinfo=UTC),
        mgrs_tile="48QTD",
        epsg=32648,
        cloud_cover=10.0,
        processing_baseline=baseline,
        generation="2025-09-01T08:00:00Z",
        footprint=None,
        hrefs={"B04": "red", "B08": "nir", "SCL": "scl"},
    )


def _dn(ndvi, red_refl=0.05, offset=1000):
    """DNs for a target NDVI with a fixed red reflectance."""
    nir = red_refl * (1 + ndvi) / (1 - ndvi)
    return np.uint16(red_refl * 10000 + offset), np.uint16(nir * 10000 + offset)


@pytest.fixture
def pg():
    return build_plot_grid(square_around(*NONG_RUEA, 500), 32648, ring_outer_m=600)


def _bands(pg, plot_ndvi, ring_ndvi):
    shape = pg.grid.shape
    red = np.zeros(shape, np.uint16)
    nir = np.zeros(shape, np.uint16)
    r1, n1 = _dn(ring_ndvi)
    red[:], nir[:] = r1, n1
    r2, n2 = _dn(plot_ndvi)
    red[pg.plot_mask], nir[pg.plot_mask] = r2, n2
    return red, nir


def test_compute_observation_plot_and_ring(pg):
    red, nir = _bands(pg, 0.55, 0.75)
    scl = np.full(pg.grid.shape, 4, np.uint8)
    cane = np.ones(pg.grid.shape, bool)
    o = compute_observation(_scene(), pg, cane, red, nir, scl, IngestConfig())
    assert o.is_clear and o.clear_fraction == 1.0
    assert o.median_ndvi == pytest.approx(0.55, abs=1e-3)
    assert o.ring_median == pytest.approx(0.75, abs=1e-3)
    assert o.ring_mad == pytest.approx(0.0, abs=1e-6)
    assert o.ring_n_px == int(pg.ring_mask.sum())


def test_compute_observation_cloudy_plot_is_dropped(pg):
    red, nir = _bands(pg, 0.7, 0.7)
    scl = np.full(pg.grid.shape, 4, np.uint8)
    rows = np.where(pg.plot_mask.any(axis=1))[0]
    scl[rows[: len(rows) // 2 + 2], :] = 9  # cloud over the top half of the plot
    o = compute_observation(_scene(), pg, None, red, nir, scl, IngestConfig())
    assert o.clear_fraction < 0.6
    assert not o.is_clear and "low_clear_fraction" in o.flags
    assert o.ring_median is None


def test_compute_observation_ring_needs_cane_pixels(pg):
    red, nir = _bands(pg, 0.7, 0.7)
    scl = np.full(pg.grid.shape, 4, np.uint8)
    cane = np.zeros(pg.grid.shape, bool)
    o = compute_observation(_scene(), pg, cane, red, nir, scl, IngestConfig())
    assert o.ring_median is None and "ring_too_few_pixels" in o.flags


def test_old_baseline_without_offset(pg):
    r, n = _dn(0.6, offset=0)
    red = np.full(pg.grid.shape, r, np.uint16)
    nir = np.full(pg.grid.shape, n, np.uint16)
    scl = np.full(pg.grid.shape, 4, np.uint8)
    o = compute_observation(_scene(baseline="03.00"), pg, None, red, nir, scl, IngestConfig())
    assert o.median_ndvi == pytest.approx(0.6, abs=1e-3)


class FakeStac:
    def __init__(self, scenes):
        self.scenes = scenes

    def sentinel2(self, geom, start, end, max_cloud):
        return self.scenes

    def worldcover_href(self, geom, year):
        return "wc"


def test_plot_ingestor_with_fake_io(tmp_path):
    geom = square_around(*NONG_RUEA, 500)
    cfg = IngestConfig(ring_outer_m=600)
    a = _scene("S2A_MSIL2A_20250901T033541_R061_T48QTD_20250901T080000")
    b = SceneRef(
        **{
            **a.__dict__,
            "item_id": "S2B_MSIL2A_20250906T033541_R061_T48QTD_X",
            "acquired_at": datetime(2025, 9, 6, 3, 35, tzinfo=UTC),
        }
    )

    def reader(href, grid):
        if href == "scl":
            return np.full(grid.shape, 4, np.uint8)
        r, n = _dn(0.72)
        return np.full(grid.shape, r if href == "red" else n, np.uint16)

    def reprojector(href, grid):
        return np.full(grid.shape, 40, np.uint8)

    ing = PlotIngestor(cfg, FakeStac([a, b]), tmp_path, reader, reprojector)
    obs, meta = ing.ingest(geom, "2025-09-01", "2025-09-30", workers=2)
    assert [o.obs_date.isoformat() for o in obs] == ["2025-09-01", "2025-09-06"]
    assert all(o.is_clear and o.median_ndvi == pytest.approx(0.72, abs=1e-3) for o in obs)
    assert meta["n_errors"] == 0 and meta["cane_masks"]["32648"]["likely_cane_ring_px"] > 0
    assert list(tmp_path.glob("cane_mask_*.npz"))  # cached
    # incremental: already-stored scenes are skipped
    obs2, meta2 = ing.ingest(geom, "2025-09-01", "2025-09-30", skip_item_ids=[a.item_id])
    assert [o.item_id for o in obs2] == [b.item_id] and meta2["n_skipped_existing"] == 1
