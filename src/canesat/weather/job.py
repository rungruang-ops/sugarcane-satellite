"""Fetch weather for registered plots, detect rain_gap/rain_back, persist alerts."""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from .. import db
from .config import RainConfig
from .detect import DayWeather, annotate_dry_streaks, consecutive_dry_ending, detect_rain_series
from .gpm import RainFetcher
from .smap import SoilFetcher

log = logging.getLogger(__name__)


def run_weather_check(
    conn,
    *,
    rain: RainFetcher,
    soil: SoilFetcher,
    cfg: RainConfig,
    plot_ids: list[int] | None = None,
    as_of: date | None = None,
    fetch_soil: bool = True,
) -> dict[str, Any]:
    """Pull recent GPM (+ optional SMAP), upsert weather_daily, create alerts."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=cfg.lookback_days - 1)
    plots = db.list_plots(conn)
    if plot_ids is not None:
        want = set(plot_ids)
        plots = [p for p in plots if int(p["id"]) in want]
    summary: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "start": start.isoformat(),
        "plots": {},
        "new_alert_ids": [],
    }

    cells: dict[tuple[float, float], list[dict]] = defaultdict(list)
    for p in plots:
        key = (round(float(p["lat"]), 1), round(float(p["lon"]), 1))
        cells[key].append(p)

    cell_rain: dict[tuple[float, float], dict[date, float | None]] = {}
    cell_sm: dict[tuple[float, float], dict[date, tuple[float | None, int | None]]] = {}
    for (lat, lon), group in cells.items():
        log.info("fetching weather for cell %.1f,%.1f (%d plots)", lat, lon, len(group))
        cell_rain[(lat, lon)] = rain.daily_precip(lat, lon, start, as_of)
        if fetch_soil:
            cell_sm[(lat, lon)] = soil.daily_soil_moisture(lat, lon, start, as_of)
        else:
            cell_sm[(lat, lon)] = {
                start + timedelta(days=i): (None, None)
                for i in range((as_of - start).days + 1)
            }

    with conn.transaction():
        for (lat, lon), group in cells.items():
            precip = cell_rain[(lat, lon)]
            smap = cell_sm[(lat, lon)]
            for p in group:
                pid = int(p["id"])
                series = [
                    DayWeather(d, precip.get(d), (smap.get(d) or (None, None))[0])
                    for d in sorted(precip)
                ]
                for day, streak in annotate_dry_streaks(series, cfg.dry_day_mm):
                    sm_q = (smap.get(day.obs_date) or (None, None))[1]
                    upsert_weather_day(
                        conn,
                        pid,
                        day.obs_date,
                        precip_mm=day.precip_mm,
                        soil_moisture=day.soil_moisture,
                        sm_quality=sm_q,
                        dry_streak=streak if day.precip_mm is not None else None,
                        source_rain="GPM_3IMERGDL.07",
                        source_sm="SPL3SMP_E.006" if day.soil_moisture is not None else None,
                    )
                previous = db.previous_alerts(conn, pid)
                water = _owner_water(conn, pid)
                found = detect_rain_series(
                    series, cfg, as_of=as_of, previous=previous, has_water_source=water
                )
                new_ids = []
                for a in found:
                    aid = db.insert_alert(
                        conn,
                        pid,
                        a.type,
                        a.severity,
                        a.obs_date,
                        z_score=None,
                        gap=None,
                        details=a.details,
                        status="pending",
                    )
                    if aid:
                        new_ids.append(aid)
                        summary["new_alert_ids"].append(aid)
                summary["plots"][pid] = {
                    "name": p["name"],
                    "lat": float(p["lat"]),
                    "lon": float(p["lon"]),
                    "n_days": sum(1 for d in series if d.precip_mm is not None),
                    "dry_streak": consecutive_dry_ending(series, as_of, cfg.dry_day_mm),
                    "soil_moisture": next(
                        (d.soil_moisture for d in reversed(series) if d.soil_moisture is not None),
                        None,
                    ),
                    "new_alert_ids": new_ids,
                    "alerts": [
                        {
                            "type": a.type,
                            "obs_date": a.obs_date.isoformat(),
                            "dry_days": a.dry_days,
                            "details": a.details,
                        }
                        for a in found
                    ],
                }
    conn.commit()
    return summary


def _owner_water(conn, plot_id: int) -> bool | None:
    row = conn.execute(
        "SELECT u.has_water_source FROM plots p LEFT JOIN users u ON u.id = p.owner_user_id"
        " WHERE p.id = %s",
        (plot_id,),
    ).fetchone()
    return None if not row else row["has_water_source"]


def upsert_weather_day(
    conn,
    plot_id: int,
    obs_date: date,
    *,
    precip_mm: float | None,
    soil_moisture: float | None,
    sm_quality: int | None,
    dry_streak: int | None,
    source_rain: str | None,
    source_sm: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO weather_daily
            (plot_id, obs_date, precip_mm, soil_moisture, sm_quality, dry_streak,
             source_rain, source_sm)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (plot_id, obs_date) DO UPDATE SET
            precip_mm = COALESCE(EXCLUDED.precip_mm, weather_daily.precip_mm),
            soil_moisture = COALESCE(EXCLUDED.soil_moisture, weather_daily.soil_moisture),
            sm_quality = COALESCE(EXCLUDED.sm_quality, weather_daily.sm_quality),
            dry_streak = COALESCE(EXCLUDED.dry_streak, weather_daily.dry_streak),
            source_rain = COALESCE(EXCLUDED.source_rain, weather_daily.source_rain),
            source_sm = COALESCE(EXCLUDED.source_sm, weather_daily.source_sm),
            fetched_at = now()
        """,
        (
            plot_id,
            obs_date,
            precip_mm,
            soil_moisture,
            sm_quality,
            dry_streak,
            source_rain,
            source_sm,
        ),
    )
