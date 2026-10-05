"""GPM IMERG Late daily (GPM_3IMERGDL) precipitation at a lat/lon via OPeNDAP DAP4."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Protocol

import numpy as np

from .earthdata import cmr_granules, granule_date, opendap_url

log = logging.getLogger(__name__)

SHORT_NAME = "GPM_3IMERGDL"
VERSION = "07"
# IMERG 0.1° grid centres: lon -179.95..179.95, lat -89.95..89.95
LON0, LAT0, STEP = -179.95, -89.95, 0.1


def lon_lat_index(lon: float, lat: float) -> tuple[int, int]:
    lon_idx = round((lon - LON0) / STEP)
    lat_idx = round((lat - LAT0) / STEP)
    lon_idx = max(0, min(3599, lon_idx))
    lat_idx = max(0, min(1799, lat_idx))
    return lon_idx, lat_idx


class RainFetcher(Protocol):
    def daily_precip(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, float | None]: ...


class GpmImergFetcher:
    """Live Earthdata fetcher. Needs EARTHDATA_TOKEN and pydap."""

    def __init__(self, session) -> None:
        self._session = session

    def daily_precip(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, float | None]:
        from pydap.client import open_url

        lon_idx, lat_idx = lon_lat_index(lon, lat)
        entries = cmr_granules(
            short_name=SHORT_NAME,
            version=VERSION,
            start=start,
            end=end,
            lon=lon,
            lat=lat,
            session=self._session,
        )
        out: dict[date, float | None] = {}
        for entry in entries:
            d = granule_date(entry)
            if d is None or d < start or d > end:
                continue
            url = opendap_url(entry)
            if not url:
                continue
            try:
                ds = open_url(url, session=self._session, protocol="dap4")
                # dims: time, lon, lat
                sample = ds["precipitation"][0, lon_idx, lat_idx]
                val = float(np.asarray(sample.data).ravel()[0])
                if val < -9000:  # fill
                    out[d] = None
                else:
                    out[d] = val
            except Exception as exc:
                log.warning("GPM read failed for %s: %s", d, type(exc).__name__)
                out[d] = None
        # fill missing calendar days as None so callers see gaps
        cur = start
        while cur <= end:
            out.setdefault(cur, None)
            cur += timedelta(days=1)
        return out


class FixtureRainFetcher:
    """Deterministic precip series for tests (mm/day by date)."""

    def __init__(self, series: dict[date, float | None]) -> None:
        self._series = series

    def daily_precip(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, float | None]:
        out: dict[date, float | None] = {}
        cur = start
        while cur <= end:
            out[cur] = self._series.get(cur)
            cur += timedelta(days=1)
        return out
