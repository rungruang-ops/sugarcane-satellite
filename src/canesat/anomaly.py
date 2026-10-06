"""Anomaly engine: 'less green than neighbours' (design §5.1). Pure functions only.

Rule (defaults, configurable in AnomalyConfig):
  gap_nb = (ndvi_plot - median_nb) / median_nb
  z_nb   = (ndvi_plot - median_nb) / (1.4826 * max(MAD_nb, mad_floor))
  red alert   : gap_nb <= -15% AND z_nb <= -2 on 2 consecutive clear observations that are
                at most 20 days apart; at most one alert per plot/type per 14 days (cooldown)
  yellow watch: condition met once, or z_hist <= -1.5 (shown in the app, never pushed)
  no alert    : crop age < 60 days; in crushing months a sudden whole-plot drop becomes a
                'harvest_check' ("already harvested?") instead of a greenness alert.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date

from .baseline import HistBaseline, NeighborBaseline
from .config import AnomalyConfig
from .ndvi import MAD_TO_SIGMA

# observation status values
NORMAL = "normal"
WATCH = "watch"
ALERT = "alert"
COOLDOWN = "alert_cooldown"  # condition met but an alert was sent recently
YOUNG = "young_cane"
NO_BASELINE = "no_baseline"
HARVEST = "harvest_check"


@dataclass(frozen=True)
class EvalPoint:
    obs_date: date
    ndvi: float
    nb: NeighborBaseline | None = None
    hist: HistBaseline | None = None
    crop_age_days: int | None = None
    ref: object = None  # opaque handle (e.g. scene_id) passed through


@dataclass
class Evaluation:
    point: EvalPoint
    gap_nb: float | None
    z_nb: float | None
    z_hist: float | None
    hit: bool
    streak: int
    status: str


@dataclass
class AlertCandidate:
    obs_date: date
    type: str  # 'greenness' | 'harvest_check'
    severity: str
    z_score: float
    gap: float
    details: dict = field(default_factory=dict)
    ref: object = None


def neighbour_metrics(ndvi: float, nb: NeighborBaseline, mad_floor: float) -> tuple[float, float]:
    gap = (ndvi - nb.median) / nb.median if nb.median else float("nan")
    z = (ndvi - nb.median) / (MAD_TO_SIGMA * max(nb.mad, mad_floor))
    return gap, z


def hist_z(ndvi: float, hist: HistBaseline | None) -> float | None:
    if hist is None or hist.std <= 0:
        return None
    return (ndvi - hist.mean) / hist.std


def evaluate_series(
    points: Sequence[EvalPoint],
    cfg: AnomalyConfig | None = None,
    previous_alerts: Iterable[tuple[str, date]] = (),
) -> tuple[list[Evaluation], list[AlertCandidate]]:
    """Evaluate a plot's clear observations in date order.

    ``previous_alerts`` are (type, obs_date) pairs already stored, so the cooldown also holds
    across incremental runs. Returns per-observation evaluations and new alert candidates.
    """
    cfg = cfg or AnomalyConfig()
    pts = sorted(points, key=lambda p: p.obs_date)
    last_alert: dict[str, date] = {}
    previous_alerts = list(previous_alerts)
    already = set(previous_alerts)
    for typ, d in previous_alerts:
        if typ not in last_alert or d > last_alert[typ]:
            last_alert[typ] = d

    evals: list[Evaluation] = []
    alerts: list[AlertCandidate] = []
    streak = 0
    prev_date: date | None = None
    recent: list[float] = []  # previous clear NDVI values (for harvest-drop test)

    for p in pts:
        gap = z = None
        if p.nb is not None:
            gap, z = neighbour_metrics(p.ndvi, p.nb, cfg.mad_floor)
        zh = hist_z(p.ndvi, p.hist)
        hit = (
            gap is not None and z is not None and gap <= cfg.gap_threshold and z <= cfg.z_threshold
        )

        contiguous = prev_date is not None and (p.obs_date - prev_date).days <= cfg.max_gap_days
        streak = (streak + 1 if contiguous else 1) if hit else 0

        young = p.crop_age_days is not None and p.crop_age_days < cfg.min_crop_age_days
        if young:
            status = YOUNG
        elif p.nb is None:
            status = WATCH if (zh is not None and zh <= cfg.watch_z_hist) else NO_BASELINE
        elif hit and streak >= cfg.consecutive_required:
            typ = "greenness"
            drop = None
            if recent:
                prior = recent[-2:]
                drop = p.ndvi - sum(prior) / len(prior)
            if (
                p.obs_date.month in cfg.harvest_months
                and drop is not None
                and drop <= cfg.harvest_drop
            ):
                typ = HARVEST
            last = last_alert.get(typ)
            if (typ, p.obs_date) in already:  # alert stored by an earlier run
                status = ALERT if typ == "greenness" else HARVEST
            elif last is not None and 0 <= (p.obs_date - last).days < cfg.cooldown_days:
                status = COOLDOWN
            else:
                status = ALERT if typ == "greenness" else HARVEST
                last_alert[typ] = p.obs_date
                alerts.append(
                    AlertCandidate(
                        obs_date=p.obs_date,
                        type=typ,
                        severity="red" if typ == "greenness" else "yellow",
                        z_score=round(z, 3),  # type: ignore[arg-type]
                        gap=round(gap, 4),  # type: ignore[arg-type]
                        details={
                            "ndvi_plot": round(p.ndvi, 4),
                            "nb_source": p.nb.source,
                            "nb_median": round(p.nb.median, 4),
                            "nb_mad": round(p.nb.mad, 4),
                            "nb_n": p.nb.n,
                            "z_hist": None if zh is None else round(zh, 3),
                            "streak": streak,
                            "drop_vs_recent": None if drop is None else round(drop, 4),
                            "crop_age_days": p.crop_age_days,
                        },
                        ref=p.ref,
                    )
                )
        elif hit or (zh is not None and zh <= cfg.watch_z_hist):
            status = WATCH
        else:
            status = NORMAL

        evals.append(Evaluation(p, gap, z, zh, hit, streak, status))
        prev_date = p.obs_date
        recent.append(p.ndvi)
    return evals, alerts


def area_suppression(
    hits_by_plot: dict[int, bool],
    tambon_by_plot: dict[int, str | None],
    cfg: AnomalyConfig | None = None,
) -> set[int]:
    """Plots whose alerts on one scene date should be suppressed because the problem looks
    area-wide (> ``area_suppress_fraction`` of the evaluated plots in the same tambon hit
    at once — likely drought or an imagery artefact). Only applies to tambons with at least
    ``area_min_plots`` evaluated plots."""
    cfg = cfg or AnomalyConfig()
    by_tambon: dict[str, list[int]] = {}
    for pid in hits_by_plot:
        t = tambon_by_plot.get(pid)
        if t:
            by_tambon.setdefault(t, []).append(pid)
    out: set[int] = set()
    for pids in by_tambon.values():
        if len(pids) < cfg.area_min_plots:
            continue
        frac = sum(hits_by_plot[p] for p in pids) / len(pids)
        if frac > cfg.area_suppress_fraction:
            out.update(p for p in pids if hits_by_plot[p])
    return out
