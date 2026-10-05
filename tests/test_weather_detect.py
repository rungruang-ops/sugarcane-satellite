"""Pure rain-gap / rain-back detection (no network, no DB)."""

from datetime import date, timedelta

from canesat.weather.config import RainConfig
from canesat.weather.detect import DayWeather, consecutive_dry_ending, detect_rain_series


def _series(precip, start=date(2026, 9, 1), sm=0.10):
    out = []
    for i, p in enumerate(precip):
        out.append(DayWeather(start + timedelta(days=i), p, sm))
    return out


def test_consecutive_dry_streak():
    s = _series([5.0] + [0.0] * 14 + [2.0])
    assert consecutive_dry_ending(s, date(2026, 9, 15), 1.0) == 14
    assert consecutive_dry_ending(s, date(2026, 9, 16), 1.0) == 0


def test_rain_gap_requires_dry_days_and_low_soil():
    cfg = RainConfig(
        gap_days=14, require_both=True, sm_dry_threshold=0.15,
        season_months=tuple(range(1, 13)),
    )
    dry = _series([0.2] * 16, sm=0.10)
    alerts = detect_rain_series(dry, cfg, as_of=date(2026, 9, 16))
    assert len(alerts) == 1 and alerts[0].type == "rain_gap" and alerts[0].dry_days == 16

    wet_soil = _series([0.2] * 16, sm=0.25)
    assert detect_rain_series(wet_soil, cfg, as_of=date(2026, 9, 16)) == []


def test_rain_gap_without_require_both():
    cfg = RainConfig(gap_days=14, require_both=False, season_months=tuple(range(1, 13)))
    dry = _series([0.0] * 14, sm=None)
    alerts = detect_rain_series(dry, cfg, as_of=date(2026, 9, 14))
    assert len(alerts) == 1


def test_rain_gap_cooldown():
    cfg = RainConfig(
        gap_days=14, require_both=False, cooldown_days=14,
        season_months=tuple(range(1, 13)),
    )
    dry = _series([0.0] * 30)  # Sep 1 .. Sep 30
    prev = [("rain_gap", date(2026, 9, 10))]
    # still within cooldown (9 days later)
    assert detect_rain_series(dry, cfg, as_of=date(2026, 9, 19), previous=prev) == []
    # cooldown elapsed (14 days later)
    assert detect_rain_series(dry, cfg, as_of=date(2026, 9, 24), previous=prev)


def test_rain_back_after_gap():
    cfg = RainConfig(
        gap_days=14, require_both=False, rain_back_3d_mm=20.0, season_months=tuple(range(1, 13))
    )
    # 14 dry then 3 wet days totaling 25 mm
    precip = [0.0] * 14 + [8.0, 9.0, 8.0]
    s = _series(precip)
    prev = [("rain_gap", date(2026, 9, 14))]
    alerts = detect_rain_series(s, cfg, as_of=date(2026, 9, 17), previous=prev)
    assert len(alerts) == 1 and alerts[0].type == "rain_back"
    assert alerts[0].precip_3d_mm == 25.0


def test_out_of_season_skips_gap():
    cfg = RainConfig(gap_days=14, require_both=False, season_months=(5, 6, 7, 8, 9, 10, 11))
    dry = _series([0.0] * 14, start=date(2026, 1, 1))
    assert detect_rain_series(dry, cfg, as_of=date(2026, 1, 14)) == []
