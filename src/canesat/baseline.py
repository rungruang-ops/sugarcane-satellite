"""Neighbour and prior-year baselines (pure functions).

Neighbour baseline, in order of preference (design §5.1):
  (a) other *registered* plots within ``nb_radius_m`` whose crop age differs by at most
      ``nb_max_age_diff_days``, observed clear in the same scene (needs >= ``nb_min_plots``);
  (b) otherwise the 'likely cane' pixels in a 2–3 km ring around the plot, same scene
      (median + MAD computed during ingestion).
Prior-year baseline: the plot's own clear observations within ±``hist_window_days`` of the
same calendar day in each of the previous ``hist_years`` years.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

from .ndvi import median_mad


@dataclass(frozen=True)
class PlotInfo:
    plot_id: int
    cane_type: str = "unknown"
    planting_date: date | None = None
    last_harvest_date: date | None = None
    tambon_code: str | None = None

    @property
    def crop_start(self) -> date | None:
        """Start of the current crop cycle: last harvest for ratoon cane, planting date for
        plant cane; whichever is known (latest) otherwise."""
        if self.cane_type == "ratoon" and self.last_harvest_date:
            return self.last_harvest_date
        if self.cane_type == "plant" and self.planting_date:
            return self.planting_date
        known = [d for d in (self.planting_date, self.last_harvest_date) if d]
        return max(known) if known else None

    def crop_age_days(self, on: date) -> int | None:
        start = self.crop_start
        if start is None or start > on:
            return None
        return (on - start).days


@dataclass(frozen=True)
class NeighborBaseline:
    source: str  # 'registered' | 'cane_pixels'
    median: float
    mad: float
    n: int


@dataclass(frozen=True)
class HistBaseline:
    mean: float
    std: float
    n_years: int
    n_obs: int


def select_peers(
    target: PlotInfo,
    candidates: Iterable[tuple[PlotInfo, float]],
    on: date,
    radius_m: float = 5000.0,
    max_age_diff_days: int = 45,
) -> list[PlotInfo]:
    """Registered plots near ``target`` (``candidates`` = (plot, distance_m)) with similar crop
    age on ``on``. Plots with unknown crop age are never peers of a plot with known age; if
    the target's age is unknown, only other unknown-age plots qualify."""
    t_age = target.crop_age_days(on)
    peers = []
    for p, dist in candidates:
        if p.plot_id == target.plot_id or dist > radius_m:
            continue
        age = p.crop_age_days(on)
        both_unknown = t_age is None and age is None
        similar = t_age is not None and age is not None and abs(age - t_age) <= max_age_diff_days
        if both_unknown or similar:
            peers.append(p)
    return peers


def registered_baseline(
    peer_values: Sequence[float], min_plots: int = 8
) -> NeighborBaseline | None:
    vals = [v for v in peer_values if v is not None]
    if len(vals) < min_plots:
        return None
    med, mad = median_mad(vals)  # type: ignore[arg-type]
    return NeighborBaseline("registered", med, mad, len(vals))


def ring_baseline(
    ring_median: float | None, ring_mad: float | None, ring_n_px: int | None
) -> NeighborBaseline | None:
    if ring_median is None or ring_mad is None or not ring_n_px:
        return None
    return NeighborBaseline("cane_pixels", float(ring_median), float(ring_mad), int(ring_n_px))


def choose_baseline(
    registered: NeighborBaseline | None, ring: NeighborBaseline | None
) -> NeighborBaseline | None:
    return registered if registered is not None else ring


def _shift_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year - years)
    except ValueError:  # 29 Feb
        return d.replace(year=d.year - years, day=28)


def historical_baseline(
    series: Iterable[tuple[date, float]],
    on: date,
    window_days: int = 10,
    years: int = 3,
    min_years: int = 1,
    std_floor: float = 0.05,
) -> HistBaseline | None:
    """Same-period baseline from the plot's own previous years.

    mean = mean of per-year medians; std = std of all window values (floored at ``std_floor``
    so a very stable history does not produce huge z-scores; mill design §6.2).
    """
    pts = list(series)
    per_year: list[float] = []
    all_vals: list[float] = []
    for k in range(1, years + 1):
        centre = _shift_years(on, k)
        lo, hi = centre - timedelta(days=window_days), centre + timedelta(days=window_days)
        vals = [v for d, v in pts if lo <= d <= hi and v is not None]
        if vals:
            per_year.append(statistics.median(vals))
            all_vals.extend(vals)
    if len(per_year) < max(1, min_years):
        return None
    mean = statistics.fmean(per_year)
    std = statistics.stdev(all_vals) if len(all_vals) > 1 else 0.0
    return HistBaseline(mean, max(std, std_floor), len(per_year), len(all_vals))
