"""Run the anomaly engine over stored observations (baseline selection + persistence)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from .anomaly import AlertCandidate, EvalPoint, Evaluation, area_suppression, evaluate_series
from .baseline import (
    PlotInfo,
    choose_baseline,
    historical_baseline,
    registered_baseline,
    ring_baseline,
    select_peers,
)
from .config import AnomalyConfig


@dataclass
class PlotResult:
    evaluations: list[Evaluation] = field(default_factory=list)
    alerts: list[AlertCandidate] = field(default_factory=list)
    suppressed_dates: set[date] = field(default_factory=set)


def evaluate_plots(
    plots: Mapping[int, PlotInfo],
    clear_obs: Mapping[int, Sequence[Mapping[str, Any]]],
    neighbours: Mapping[int, Sequence[tuple[int, float]]],
    cfg: AnomalyConfig,
    previous: Mapping[int, Sequence[tuple[str, date]]] | None = None,
    target_ids: Sequence[int] | None = None,
) -> dict[int, PlotResult]:
    """Pure core of `canesat detect`.

    ``clear_obs[pid]`` rows need: obs_date, median_ndvi, ring_median, ring_mad, ring_n_px and
    an optional scene_id. ``neighbours[pid]`` = [(other_pid, distance_m)].
    """
    previous = previous or {}
    ndvi_at: dict[tuple[int, date], float] = {}
    for pid, rows in clear_obs.items():
        for r in rows:
            if r["median_ndvi"] is not None:
                ndvi_at[(pid, r["obs_date"])] = float(r["median_ndvi"])

    results: dict[int, PlotResult] = {}
    for pid in target_ids if target_ids is not None else list(plots):
        plot = plots[pid]
        rows = [r for r in clear_obs.get(pid, []) if r["median_ndvi"] is not None]
        series = [(r["obs_date"], float(r["median_ndvi"])) for r in rows]
        cands = [(plots[q], dist) for q, dist in neighbours.get(pid, []) if q in plots]
        points = []
        for r in rows:
            d = r["obs_date"]
            peers = select_peers(plot, cands, d, cfg.nb_radius_m, cfg.nb_max_age_diff_days)
            peer_vals = [ndvi_at[(p.plot_id, d)] for p in peers if (p.plot_id, d) in ndvi_at]
            nb = choose_baseline(
                registered_baseline(peer_vals, cfg.nb_min_plots),
                ring_baseline(r.get("ring_median"), r.get("ring_mad"), r.get("ring_n_px")),
            )
            hist = historical_baseline(
                series,
                d,
                cfg.hist_window_days,
                cfg.hist_years,
                cfg.hist_min_years,
                cfg.hist_std_floor,
            )
            points.append(
                EvalPoint(
                    d,
                    float(r["median_ndvi"]),
                    nb,
                    hist,
                    plot.crop_age_days(d),
                    ref=r.get("scene_id"),
                )
            )
        evals, alerts = evaluate_series(points, cfg, previous.get(pid, ()))
        results[pid] = PlotResult(evals, alerts)

    # area-wide suppression, per scene date
    hits: dict[date, dict[int, bool]] = defaultdict(dict)
    for pid, res in results.items():
        for ev in res.evaluations:
            if ev.point.nb is not None:
                hits[ev.point.obs_date][pid] = ev.hit
    tambons = {pid: p.tambon_code for pid, p in plots.items()}
    for d, by_plot in hits.items():
        for pid in area_suppression(by_plot, tambons, cfg):
            results[pid].suppressed_dates.add(d)
    return results


def _r(x: float | None, nd: int = 4) -> float | None:
    return None if x is None else round(float(x), nd)


def run_detect(conn, plot_ids: Sequence[int] | None, cfg: AnomalyConfig) -> dict[str, Any]:
    """Load from DB, evaluate, write evaluation columns and new alerts. Returns a summary."""
    from . import db

    all_plots = {int(r["id"]): db.plot_info(r) for r in db.list_plots(conn)}
    targets = list(plot_ids) if plot_ids else list(all_plots)
    neighbours = {pid: db.plot_distances(conn, pid, cfg.nb_radius_m) for pid in targets}
    involved = set(targets) | {q for v in neighbours.values() for q, _ in v}
    rows = db.fetch_observations(conn, sorted(involved), clear_only=True)
    clear_obs: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        clear_obs[int(r["plot_id"])].append(r)
    previous = {pid: db.previous_alerts(conn, pid) for pid in targets}

    results = evaluate_plots(all_plots, clear_obs, neighbours, cfg, previous, targets)
    summary: dict[str, Any] = {"plots": {}}
    with conn.transaction():
        for pid, res in results.items():
            counts: dict[str, int] = defaultdict(int)
            for ev in res.evaluations:
                status = ev.status
                if ev.point.obs_date in res.suppressed_dates and ev.hit:
                    status = "suppressed_area"
                counts[status] += 1
                nb, hist = ev.point.nb, ev.point.hist
                db.update_evaluation(
                    conn,
                    pid,
                    ev.point.ref,
                    {
                        "nb_source": nb.source if nb else None,
                        "nb_median": _r(nb.median) if nb else None,
                        "nb_mad": _r(nb.mad) if nb else None,
                        "nb_n": nb.n if nb else None,
                        "gap_nb": _r(ev.gap_nb),
                        "z_nb": _r(ev.z_nb, 3),
                        "hist_mean": _r(hist.mean) if hist else None,
                        "hist_n_years": hist.n_years if hist else None,
                        "z_hist": _r(ev.z_hist, 3),
                        "status": status,
                    },
                )
            new_ids = []
            for a in res.alerts:
                suppressed = a.obs_date in res.suppressed_dates
                details = {
                    **a.details,
                    "thresholds": {
                        "gap": cfg.gap_threshold,
                        "z": cfg.z_threshold,
                        "consecutive": cfg.consecutive_required,
                        "cooldown_days": cfg.cooldown_days,
                    },
                }
                if suppressed:
                    details["suppressed_reason"] = "area_wide"
                aid = db.insert_alert(
                    conn,
                    pid,
                    a.type,
                    a.severity,
                    a.obs_date,
                    a.z_score,
                    a.gap,
                    details,
                    status="suppressed" if suppressed else "pending",
                )
                if aid:
                    new_ids.append(aid)
            summary["plots"][pid] = {
                "n_clear_obs": len(res.evaluations),
                "status_counts": dict(counts),
                "new_alert_ids": new_ids,
            }
    conn.commit()
    return summary
