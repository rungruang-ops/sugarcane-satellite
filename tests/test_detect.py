from datetime import date, timedelta

from canesat.baseline import PlotInfo
from canesat.config import AnomalyConfig
from canesat.detect import evaluate_plots

D0 = date(2025, 8, 1)
DATES = [D0 + timedelta(days=5 * i) for i in range(4)]


def rows(values, ring=(0.75, 0.03, 5000)):
    return [
        {
            "obs_date": d,
            "median_ndvi": v,
            "ring_median": ring[0],
            "ring_mad": ring[1],
            "ring_n_px": ring[2],
            "scene_id": i,
        }
        for i, (d, v) in enumerate(zip(DATES, values, strict=True))
    ]


def test_ring_baseline_used_when_few_registered_plots():
    plots = {1: PlotInfo(1), 2: PlotInfo(2)}
    obs = {1: rows([0.74, 0.59, 0.58, 0.74]), 2: rows([0.74] * 4)}
    res = evaluate_plots(plots, obs, {1: [(2, 300.0)], 2: [(1, 300.0)]}, AnomalyConfig())
    assert {e.point.nb.source for e in res[1].evaluations} == {"cane_pixels"}
    assert [a.obs_date for a in res[1].alerts] == [DATES[2]]
    assert res[2].alerts == []


def test_registered_neighbours_used_when_enough():
    # target looks fine vs the cane-pixel ring (0.60) but is clearly below 9 registered peers
    plots = {i: PlotInfo(i) for i in range(1, 11)}
    obs = {1: rows([0.74, 0.59, 0.58, 0.74], ring=(0.60, 0.05, 5000))}
    peer_vals = [0.72, 0.73, 0.74, 0.75, 0.76, 0.74, 0.73, 0.75, 0.74]
    for pid, v in zip(range(2, 11), peer_vals, strict=True):
        obs[pid] = rows([v] * 4)
    nbrs = {1: [(p, 1000.0) for p in range(2, 11)]}
    res = evaluate_plots(plots, obs, nbrs, AnomalyConfig(), target_ids=[1])
    ev = res[1].evaluations
    assert {e.point.nb.source for e in ev} == {"registered"}
    assert ev[0].point.nb.n == 9
    assert [a.obs_date for a in res[1].alerts] == [DATES[2]]


def test_area_wide_hits_are_suppressed():
    plots = {i: PlotInfo(i, tambon_code="400501") for i in range(1, 6)}
    obs = {i: rows([0.74, 0.58, 0.57, 0.57]) for i in range(1, 4)}
    obs |= {i: rows([0.74] * 4) for i in (4, 5)}
    res = evaluate_plots(plots, obs, {}, AnomalyConfig())
    assert DATES[2] in res[1].suppressed_dates
    assert res[1].alerts  # candidates are still returned; persistence marks them suppressed
