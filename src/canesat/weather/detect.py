"""Pure rain-gap / rain-back detection over a daily weather series (no I/O)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from .config import RainConfig


@dataclass(frozen=True)
class DayWeather:
    obs_date: date
    precip_mm: float | None
    soil_moisture: float | None = None


@dataclass
class RainAlert:
    type: str  # rain_gap | rain_back
    severity: str  # orange
    obs_date: date
    dry_days: int | None = None
    precip_3d_mm: float | None = None
    soil_moisture: float | None = None
    details: dict[str, Any] = field(default_factory=dict)


def consecutive_dry_ending(
    series: Sequence[DayWeather], end: date, dry_day_mm: float
) -> int:
    """Count consecutive dry days ending on ``end`` (missing precip breaks the streak)."""
    by_date = {d.obs_date: d for d in series}
    streak = 0
    cur = end
    while True:
        day = by_date.get(cur)
        if day is None or day.precip_mm is None:
            break
        if day.precip_mm >= dry_day_mm:
            break
        streak += 1
        cur -= timedelta(days=1)
    return streak


def precip_sum(series: Sequence[DayWeather], start: date, end: date) -> float | None:
    """Sum precip over [start, end]; None if any day missing."""
    by_date = {d.obs_date: d for d in series}
    total = 0.0
    cur = start
    while cur <= end:
        day = by_date.get(cur)
        if day is None or day.precip_mm is None:
            return None
        total += float(day.precip_mm)
        cur += timedelta(days=1)
    return total


def latest_soil_moisture(
    series: Sequence[DayWeather], on_or_before: date, window: int = 5
) -> float | None:
    """Most recent non-null soil moisture in [on_or_before - window + 1, on_or_before]."""
    by_date = {d.obs_date: d for d in series}
    for i in range(window):
        d = on_or_before - timedelta(days=i)
        day = by_date.get(d)
        if day is not None and day.soil_moisture is not None:
            return float(day.soil_moisture)
    return None


def detect_rain_series(
    series: Sequence[DayWeather],
    cfg: RainConfig,
    *,
    as_of: date | None = None,
    previous: Sequence[tuple[str, date]] = (),
    has_water_source: bool | None = None,
    in_season: bool | None = None,
) -> list[RainAlert]:
    """Evaluate rain_gap / rain_back for the latest day in ``series`` (or ``as_of``).

    ``previous`` = [(alert_type, obs_date), ...] already stored (non-suppressed).
    """
    if not series:
        return []
    ordered = sorted(series, key=lambda d: d.obs_date)
    as_of = as_of or ordered[-1].obs_date
    if in_season is None:
        in_season = as_of.month in cfg.season_months

    alerts: list[RainAlert] = []
    dry = consecutive_dry_ending(ordered, as_of, cfg.dry_day_mm)
    sm = latest_soil_moisture(ordered, as_of)
    sm_dry = sm is not None and sm < cfg.sm_dry_threshold

    # cooldown check for rain_gap
    last_gap = max((d for t, d in previous if t == "rain_gap"), default=None)
    gap_cooled = last_gap is None or (as_of - last_gap).days >= cfg.cooldown_days

    gap_ok = dry >= cfg.gap_days and gap_cooled and in_season
    if gap_ok and cfg.require_both:
        gap_ok = sm_dry
    elif gap_ok and not cfg.require_both:
        gap_ok = True

    if gap_ok:
        guidance = _guidance_gap(has_water_source)
        alerts.append(
            RainAlert(
                type="rain_gap",
                severity="orange",
                obs_date=as_of,
                dry_days=dry,
                soil_moisture=sm,
                details={
                    "dry_days": dry,
                    "dry_day_mm": cfg.dry_day_mm,
                    "soil_moisture": sm,
                    "sm_dry_threshold": cfg.sm_dry_threshold,
                    "sm_dry": sm_dry,
                    "require_both": cfg.require_both,
                    "has_water_source": has_water_source,
                    "guidance": guidance,
                    "thresholds": cfg.to_dict(),
                },
            )
        )

    # rain_back: only after a prior rain_gap (pending/sent), when 3-day rain recovers
    last_gap_any = max((d for t, d in previous if t == "rain_gap"), default=None)
    last_back = max((d for t, d in previous if t == "rain_back"), default=None)
    can_rain_back = (
        last_gap_any is not None
        and last_gap_any < as_of
        and (last_back is None or last_back < last_gap_any)
    )
    if can_rain_back:
        p3 = precip_sum(ordered, as_of - timedelta(days=2), as_of)
        if p3 is not None and p3 >= cfg.rain_back_3d_mm:
            alerts.append(
                    RainAlert(
                        type="rain_back",
                        severity="orange",
                        obs_date=as_of,
                        precip_3d_mm=p3,
                        soil_moisture=sm,
                        details={
                            "precip_3d_mm": p3,
                            "rain_back_3d_mm": cfg.rain_back_3d_mm,
                            "prior_gap_date": last_gap_any.isoformat(),
                            "guidance": (
                                "ดินน่าจะชื้นพอ — ถ้าวางแผนใส่ปุ๋ยไว้ ช่วงนี้เป็นจังหวะที่ดี"
                            ),
                            "thresholds": cfg.to_dict(),
                        },
                    )
                )
    return alerts


def _guidance_gap(has_water_source: bool | None) -> list[str]:
    if has_water_source is True:
        return [
            "ถ้ามีน้ำ: ให้น้ำอ้อยอายุน้อยก่อน ช่วงเช้า/เย็น",
            "ยังไม่ต้องใส่ปุ๋ยเคมี รอดินชื้นก่อนจะคุ้มกว่า",
        ]
    if has_water_source is False:
        return [
            "ยังไม่ใส่ปุ๋ยเคมี",
            "คลุมดินด้วยใบอ้อย ลดวัชพืชที่แย่งน้ำ",
        ]
    return [
        "ถ้ามีน้ำ: ให้น้ำอ้อยอายุน้อยก่อน ช่วงเช้า/เย็น",
        "ยังไม่ต้องใส่ปุ๋ยเคมี รอดินชื้นก่อนจะคุ้มกว่า",
        "ถ้าไม่มีน้ำ: คลุมดินด้วยใบอ้อย ลดวัชพืชที่แย่งน้ำ",
    ]


def annotate_dry_streaks(
    series: Sequence[DayWeather], dry_day_mm: float
) -> list[tuple[DayWeather, int]]:
    """Return (day, dry_streak_ending_that_day) for each day with precip."""
    out = []
    for d in sorted(series, key=lambda x: x.obs_date):
        if d.precip_mm is None:
            out.append((d, 0))
        else:
            out.append((d, consecutive_dry_ending(series, d.obs_date, dry_day_mm)))
    return out
