"""Optional live read-only smoke against GPM IMERG (skipped without EARTHDATA_TOKEN)."""

from datetime import date, timedelta

import pytest

from canesat.weather.earthdata import earthdata_session, earthdata_token
from canesat.weather.gpm import GpmImergFetcher

pytestmark = pytest.mark.earthdata


def test_gpm_one_day_khon_kaen():
    assert earthdata_token()
    end = date.today() - timedelta(days=3)  # Late product lag
    start = end
    rain = GpmImergFetcher(earthdata_session())
    out = rain.daily_precip(16.55, 102.45, start, end)
    assert end in out
    # value may be None on fill, but key must exist
    assert out[end] is None or out[end] >= 0
