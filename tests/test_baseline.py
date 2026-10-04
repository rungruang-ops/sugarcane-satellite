from datetime import date

import pytest

from canesat.baseline import (
    PlotInfo,
    choose_baseline,
    historical_baseline,
    registered_baseline,
    ring_baseline,
    select_peers,
)


def test_crop_start_and_age():
    ratoon = PlotInfo(
        1, "ratoon", planting_date=date(2023, 11, 1), last_harvest_date=date(2026, 1, 15)
    )
    plant = PlotInfo(2, "plant", planting_date=date(2025, 11, 1))
    unknown = PlotInfo(3)
    assert ratoon.crop_start == date(2026, 1, 15)
    assert plant.crop_age_days(date(2026, 1, 1)) == 61
    assert unknown.crop_age_days(date(2026, 1, 1)) is None
    assert plant.crop_age_days(date(2025, 1, 1)) is None  # before planting


def test_select_peers_by_distance_and_age():
    on = date(2026, 6, 1)
    t = PlotInfo(1, "plant", planting_date=date(2025, 11, 1))
    near_same = PlotInfo(2, "ratoon", last_harvest_date=date(2025, 12, 1))  # 30 d diff
    near_old = PlotInfo(3, "plant", planting_date=date(2025, 9, 1))  # 61 d diff
    far_same = PlotInfo(4, "plant", planting_date=date(2025, 11, 5))
    unknown = PlotInfo(5)
    cands = [(near_same, 800), (near_old, 900), (far_same, 7000), (unknown, 100), (t, 0)]
    assert [p.plot_id for p in select_peers(t, cands, on)] == [2]
    # unknown-age target only compares with unknown-age plots
    assert [p.plot_id for p in select_peers(unknown, cands, on)] == []
    assert [p.plot_id for p in select_peers(PlotInfo(9), cands, on)] == [5]


def test_registered_baseline_needs_min_plots():
    assert registered_baseline([0.7] * 7, min_plots=8) is None
    b = registered_baseline([0.70, 0.72, 0.74, 0.76, 0.78, 0.80, 0.71, 0.73], 8)
    assert b.source == "registered" and b.n == 8
    assert b.median == pytest.approx(0.735)


def test_choose_prefers_registered():
    ring = ring_baseline(0.75, 0.03, 4000)
    reg = registered_baseline([0.7] * 8)
    assert choose_baseline(reg, ring) is reg
    assert choose_baseline(None, ring) is ring
    assert ring_baseline(None, None, 0) is None


def test_historical_baseline_same_period_prior_years():
    series = [
        (date(2024, 9, 25), 0.70),  # 1 year back, within ±10 d of 2025-09-30
        (date(2024, 10, 5), 0.72),
        (date(2023, 9, 28), 0.80),  # 2 years back
        (date(2024, 8, 1), 0.10),  # outside window
        (date(2025, 9, 20), 0.30),  # same year: ignored
    ]
    h = historical_baseline(series, date(2025, 9, 30), window_days=10, years=3)
    assert h.n_years == 2 and h.n_obs == 3
    assert h.mean == pytest.approx((0.71 + 0.80) / 2)
    assert h.std >= 0.05
    assert historical_baseline(series, date(2025, 9, 30), min_years=3) is None
    assert historical_baseline([], date(2025, 9, 30)) is None


def test_historical_std_floor():
    series = [(date(2024, 9, 30), 0.75), (date(2023, 9, 30), 0.75)]
    h = historical_baseline(series, date(2025, 9, 30), std_floor=0.05)
    assert h.std == 0.05
