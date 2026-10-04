"""Live Planetary Computer tests — skipped by default. Run: pytest -m network"""

import pytest

from canesat.geometry import square_around
from canesat.ingest import PlotIngestor

pytestmark = pytest.mark.network


def test_live_ingest_nong_ruea_two_weeks(tmp_path):
    geom = square_around(16.5589, 102.4372, 1000)
    obs, meta = PlotIngestor(cache_dir=tmp_path).ingest(geom, "2025-11-25", "2025-12-08")
    assert meta["n_errors"] == 0 and obs
    assert all(o.mgrs_tile == "48QTD" for o in obs)  # own UTM zone tile preferred over 47QRU
    clear = [o for o in obs if o.is_clear]
    assert clear, "expected at least one clear early-December scene"
    for o in clear:
        assert 0.0 < o.median_ndvi < 1.0
        assert o.ring_median is not None and o.ring_n_px >= 500
