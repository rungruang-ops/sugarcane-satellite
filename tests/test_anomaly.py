"""Anomaly engine on synthetic series (no network, no DB)."""

from datetime import date, timedelta

import pytest

from canesat.anomaly import (
    ALERT,
    COOLDOWN,
    HARVEST,
    NO_BASELINE,
    NORMAL,
    WATCH,
    YOUNG,
    EvalPoint,
    area_suppression,
    evaluate_series,
    neighbour_metrics,
)
from canesat.baseline import HistBaseline, NeighborBaseline
from canesat.config import AnomalyConfig

NB = NeighborBaseline("cane_pixels", median=0.75, mad=0.03, n=5000)
CFG = AnomalyConfig()


def series(values, start=date(2025, 8, 1), step=5, nb=NB, age=200, hist=None):
    return [
        EvalPoint(start + timedelta(days=i * step), v, nb, hist, age, ref=i)
        for i, v in enumerate(values)
    ]


def statuses(evals):
    return [e.status for e in evals]


def test_metrics_formula():
    gap, z = neighbour_metrics(0.60, NB, 0.02)
    assert gap == pytest.approx(-0.2)
    assert z == pytest.approx(-0.15 / (1.4826 * 0.03))


def test_healthy_plot_never_alerts():
    evals, alerts = evaluate_series(series([0.74, 0.76, 0.73, 0.75, 0.77]))
    assert alerts == [] and set(statuses(evals)) == {NORMAL}


def test_single_low_observation_is_watch_only():
    evals, alerts = evaluate_series(series([0.75, 0.58, 0.75, 0.74]))
    assert alerts == []
    assert statuses(evals) == [NORMAL, WATCH, NORMAL, NORMAL]


def test_two_consecutive_low_observations_alert():
    evals, alerts = evaluate_series(series([0.75, 0.60, 0.58, 0.57]))
    assert statuses(evals)[:3] == [NORMAL, WATCH, ALERT]
    assert len(alerts) == 1
    a = alerts[0]
    assert a.obs_date == date(2025, 8, 11) and a.type == "greenness" and a.severity == "red"
    assert a.gap <= -0.15 and a.z_score <= -2 and a.details["streak"] == 2
    assert a.ref == 2
    # 4th low obs is 5 days later: inside the 14-day cooldown
    assert statuses(evals)[3] == COOLDOWN


def test_gap_threshold_needed_even_if_z_low():
    # -10 % below neighbours with a very tight neighbour MAD: z is very low but gap is not
    nb = NeighborBaseline("cane_pixels", 0.75, 0.005, 100)
    _, alerts = evaluate_series(series([0.675, 0.675, 0.675], nb=nb))
    assert alerts == []


def test_z_threshold_needed_even_if_gap_large():
    # -20 % but neighbours are very heterogeneous (MAD 0.1 -> z ≈ -1.0)
    nb = NeighborBaseline("cane_pixels", 0.75, 0.10, 100)
    evals, alerts = evaluate_series(series([0.60, 0.60, 0.60], nb=nb))
    assert alerts == [] and set(statuses(evals)) == {NORMAL}


def test_consecutive_observations_must_be_within_20_days():
    pts = series([0.58, 0.58], step=25)
    evals, alerts = evaluate_series(pts)
    assert alerts == [] and statuses(evals) == [WATCH, WATCH]
    assert [e.streak for e in evals] == [1, 1]


def test_normal_observation_breaks_streak():
    _, alerts = evaluate_series(series([0.58, 0.74, 0.58, 0.75]))
    assert alerts == []


def test_cooldown_then_realert():
    # low throughout, every 5 days for 40 days
    pts = series([0.58] * 9)
    evals, alerts = evaluate_series(pts)
    assert [a.obs_date for a in alerts] == [date(2025, 8, 6), date(2025, 8, 21), date(2025, 9, 5)]
    assert statuses(evals) == [
        WATCH,
        ALERT,
        COOLDOWN,
        COOLDOWN,
        ALERT,
        COOLDOWN,
        COOLDOWN,
        ALERT,
        COOLDOWN,
    ]


def test_cooldown_configurable():
    _, alerts = evaluate_series(series([0.58] * 9), AnomalyConfig(cooldown_days=60))
    assert len(alerts) == 1


def test_cooldown_respects_alerts_from_previous_runs():
    pts = series([0.58, 0.58], start=date(2025, 8, 20))
    _, alerts = evaluate_series(pts, previous_alerts=[("greenness", date(2025, 8, 15))])
    assert alerts == []


def test_rerun_is_idempotent():
    pts = series([0.75, 0.60, 0.58])
    _, first = evaluate_series(pts)
    evals, again = evaluate_series(pts, previous_alerts=[(a.type, a.obs_date) for a in first])
    assert again == [] and statuses(evals)[2] == ALERT


def test_young_cane_is_not_alerted():
    evals, alerts = evaluate_series(series([0.4, 0.4, 0.4], age=30))
    assert alerts == [] and set(statuses(evals)) == {YOUNG}


def test_harvest_season_drop_becomes_harvest_check():
    pts = series([0.78, 0.77, 0.45, 0.30], start=date(2026, 1, 5))
    evals, alerts = evaluate_series(pts)
    assert [a.type for a in alerts] == ["harvest_check"]
    assert alerts[0].severity == "yellow"
    assert statuses(evals)[3] == HARVEST


def test_no_baseline_and_hist_watch():
    hist = HistBaseline(mean=0.78, std=0.05, n_years=2, n_obs=6)
    pts = series([0.77, 0.65], nb=None, hist=hist)
    evals, alerts = evaluate_series(pts)
    assert alerts == []
    assert statuses(evals) == [NO_BASELINE, WATCH]
    assert evals[1].z_hist == pytest.approx(-2.6)


def test_thresholds_configurable():
    cfg = AnomalyConfig.from_mapping({"gap_threshold": -0.25, "consecutive_required": 3})
    _, alerts = evaluate_series(series([0.60, 0.60, 0.60]), cfg)
    assert alerts == []
    _, alerts = evaluate_series(series([0.50, 0.50, 0.50]), cfg)
    assert len(alerts) == 1 and alerts[0].details["streak"] == 3


def test_unknown_config_key_rejected():
    with pytest.raises(ValueError, match="unknown"):
        AnomalyConfig.from_mapping({"gap_treshold": -0.2})


def test_area_suppression():
    hits = {1: True, 2: True, 3: True, 4: False, 5: False, 6: True}
    tam = dict.fromkeys(range(1, 6), "400501") | {6: "400502"}
    # tambon 400501: 3/5 = 60 % hit > 40 % -> suppress its hits; 400502 has too few plots
    assert area_suppression(hits, tam, CFG) == {1, 2, 3}
    assert area_suppression({1: True, 2: False}, {1: "x", 2: "x"}, CFG) == set()
    two_of_five = {1: True, 2: True, 3: False, 4: False, 5: False}
    assert area_suppression(two_of_five, tam, CFG) == set()
