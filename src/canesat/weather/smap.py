"""SMAP SPL3SMP_E enhanced L3 soil moisture (9 km) at a lat/lon via OPeNDAP DAP4."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Protocol

import numpy as np
from pyproj import Transformer

from .earthdata import cmr_granules, granule_date, opendap_url

log = logging.getLogger(__name__)

SHORT_NAME = "SPL3SMP_E"
VERSION = "006"
# EASE-Grid 2.0 Global 9 km (EPSG:6933)
_CELL = 9008.055210149286
_TO_EASE = Transformer.from_crs("EPSG:4326", "EPSG:6933", always_xy=True)


def ease_row_col(lat: float, lon: float) -> tuple[int, int]:
    """Row/col indices into SPL3SMP_E (1624 × 3856) for a WGS84 point."""
    x, y = _TO_EASE.transform(lon, lat)
    col = round((x / _CELL) + 1927.5)
    row = round(811.5 - (y / _CELL))
    col = max(0, min(3855, col))
    row = max(0, min(1623, row))
    return row, col


class SoilFetcher(Protocol):
    def daily_soil_moisture(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, tuple[float | None, int | None]]:
        """Return obs_date -> (soil_moisture m³/m³ or None, retrieval_qual_flag or None)."""
        ...


class SmapFetcher:
    def __init__(self, session) -> None:
        self._session = session

    def daily_soil_moisture(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, tuple[float | None, int | None]]:
        from pydap.client import open_url

        row, col = ease_row_col(lat, lon)
        entries = cmr_granules(
            short_name=SHORT_NAME,
            version=VERSION,
            start=start,
            end=end,
            lon=lon,
            lat=lat,
            session=self._session,
        )
        out: dict[date, tuple[float | None, int | None]] = {}
        for entry in entries:
            d = granule_date(entry)
            if d is None or d < start or d > end:
                continue
            url = opendap_url(entry)
            if not url:
                continue
            try:
                ds = open_url(url, session=self._session, protocol="dap4")
                am = ds["Soil_Moisture_Retrieval_Data_AM"]
                sm = float(np.asarray(am["soil_moisture"][row, col].data).ravel()[0])
                qual = int(np.asarray(am["retrieval_qual_flag"][row, col].data).ravel()[0])
                if sm < -9000 or sm != sm:  # fill / NaN
                    out[d] = (None, qual)
                else:
                    out[d] = (sm, qual)
            except Exception as exc:
                log.warning("SMAP read failed for %s: %s", d, type(exc).__name__)
                out[d] = (None, None)
        cur = start
        while cur <= end:
            out.setdefault(cur, (None, None))
            cur += timedelta(days=1)
        return out


class FixtureSoilFetcher:
    def __init__(self, series: dict[date, tuple[float | None, int | None]]) -> None:
        self._series = series

    def daily_soil_moisture(
        self, lat: float, lon: float, start: date, end: date
    ) -> dict[date, tuple[float | None, int | None]]:
        out: dict[date, tuple[float | None, int | None]] = {}
        cur = start
        while cur <= end:
            out[cur] = self._series.get(cur, (None, None))
            cur += timedelta(days=1)
        return out
