"""NASA Earthdata Login (EDL) bearer-token session + CMR granule search."""

from __future__ import annotations

import logging
import os
from datetime import date
from typing import Any
from urllib.parse import urlencode

import requests

log = logging.getLogger(__name__)

CMR_GRANULES = "https://cmr.earthdata.nasa.gov/search/granules.json"


def earthdata_token() -> str | None:
    return os.environ.get("EARTHDATA_TOKEN") or None


def earthdata_session(token: str | None = None) -> requests.Session:
    tok = token if token is not None else earthdata_token()
    if not tok:
        raise RuntimeError("EARTHDATA_TOKEN is not set")
    s = requests.Session()
    s.headers["Authorization"] = f"Bearer {tok}"
    s.headers["User-Agent"] = "canesat-bernghai/0.1 (rain-gap)"
    return s


def cmr_granules(
    *,
    short_name: str,
    version: str,
    start: date,
    end: date,
    lon: float,
    lat: float,
    page_size: int = 50,
    session: requests.Session | None = None,
) -> list[dict[str, Any]]:
    """Search CMR for granules covering a point over [start, end] (inclusive)."""
    # tiny bbox around the point so CMR spatial filter matches
    pad = 0.05
    params = {
        "short_name": short_name,
        "version": version,
        "temporal": f"{start.isoformat()}T00:00:00Z,{end.isoformat()}T23:59:59Z",
        "bounding_box": f"{lon - pad},{lat - pad},{lon + pad},{lat + pad}",
        "page_size": page_size,
        "sort_key": "start_date",
    }
    sess = session or requests.Session()
    r = sess.get(CMR_GRANULES, params=params, timeout=60)
    r.raise_for_status()
    return list(r.json().get("feed", {}).get("entry", []))


def opendap_url(entry: dict[str, Any]) -> str | None:
    for link in entry.get("links", []):
        href = link.get("href") or ""
        if "opendap.earthdata.nasa.gov" in href:
            return href
    return None


def granule_date(entry: dict[str, Any]) -> date | None:
    """Best-effort acquisition date from CMR entry time_start."""
    ts = entry.get("time_start") or entry.get("time_end")
    if not ts:
        return None
    return date.fromisoformat(ts[:10])


def cmr_query_url(**kwargs: Any) -> str:
    return f"{CMR_GRANULES}?{urlencode(kwargs)}"
