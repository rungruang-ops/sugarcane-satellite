"""Configuration: pipeline settings and tunable anomaly thresholds.

Defaults follow docs/design.md §5.1 (farmer-only design). All values are *starting points
for tuning*, not validated agronomic thresholds.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any

# Sentinel-2 L2A Scene Classification Layer classes
SCL_NO_DATA = 0
SCL_SATURATED = 1
SCL_DARK = 2
SCL_CLOUD_SHADOW = 3
SCL_VEGETATION = 4
SCL_NOT_VEGETATED = 5
SCL_WATER = 6
SCL_UNCLASSIFIED = 7
SCL_CLOUD_MEDIUM = 8
SCL_CLOUD_HIGH = 9
SCL_THIN_CIRRUS = 10
SCL_SNOW = 11

# ESA WorldCover class 40 = cropland
WORLDCOVER_CROPLAND = 40


@dataclass(frozen=True)
class IngestConfig:
    """How a plot's per-scene NDVI statistics are computed."""

    stac_url: str = "https://planetarycomputer.microsoft.com/api/stac/v1"
    collection: str = "sentinel-2-l2a"
    max_scene_cloud: float = 95.0  # STAC pre-filter only; real filtering is per-plot SCL
    clear_classes: tuple[int, ...] = (SCL_VEGETATION, SCL_NOT_VEGETATED)
    cloud_classes: tuple[int, ...] = (
        SCL_CLOUD_SHADOW,
        SCL_CLOUD_MEDIUM,
        SCL_CLOUD_HIGH,
        SCL_THIN_CIRRUS,
    )
    cloud_dilate_px: int = 1  # grow cloud/shadow mask by N 10 m pixels (design: 1–2)
    inner_buffer_m: float = 10.0  # shrink plot polygon to avoid mixed edge pixels
    min_clear_fraction: float = 0.6
    min_clear_px: int = 4
    # Ring of "likely cane" pixels around the plot = neighbour baseline fallback
    ring_gap_m: float = 50.0  # exclude pixels within this distance of the plot edge
    ring_outer_m: float = 2500.0  # design: 2–3 km
    ring_min_clear_px: int = 500  # >= 5 ha of clear likely-cane pixels in the ring
    # Likely-cane mask: WorldCover 2021 cropland AND NDVI > threshold on an early-Dec scene
    cane_ndvi_threshold: float = 0.6
    cane_ref_window: str = "2025-11-20/2025-12-20"
    worldcover_collection: str = "esa-worldcover"
    worldcover_year: str = "2021"


@dataclass(frozen=True)
class AnomalyConfig:
    """Thresholds for the 'less green than neighbours' alert (design §5.1)."""

    gap_threshold: float = -0.15  # gap_nb <= -15 %
    z_threshold: float = -2.0  # robust z_nb <= -2
    consecutive_required: int = 2  # consecutive clear observations meeting both
    max_gap_days: int = 20  # ...no more than this many days apart
    cooldown_days: int = 14  # max one alert per plot per type per 14 days
    watch_z_hist: float = -1.5  # yellow/watch only (no push)
    min_crop_age_days: int = 60  # no alerts for young cane (canopy not closed)
    harvest_months: tuple[int, ...] = (12, 1, 2, 3, 4)  # crushing season (typical)
    harvest_drop: float = -0.15  # sudden drop vs recent obs during crushing -> harvest_check
    mad_floor: float = 0.02  # avoid z blow-up when neighbours are very uniform
    # neighbour selection (registered plots)
    nb_min_plots: int = 8
    nb_radius_m: float = 5000.0
    nb_max_age_diff_days: int = 45
    # prior-years same-period baseline
    hist_window_days: int = 10
    hist_years: int = 3
    hist_min_years: int = 1
    hist_std_floor: float = 0.05
    # area-wide suppression (likely drought / imagery issue, not plot-specific)
    area_suppress_fraction: float = 0.4
    area_min_plots: int = 5

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> AnomalyConfig:
        return cls().merged(data or {})

    def merged(self, data: dict[str, Any]) -> AnomalyConfig:
        known = {f.name for f in fields(self)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown anomaly config keys: {sorted(unknown)}")
        clean = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items()}
        return replace(self, **clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class Settings:
    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL"))
    cache_dir: Path = field(
        default_factory=lambda: Path(os.environ.get("CANESAT_CACHE_DIR", ".cache/canesat"))
    )


def load_toml_config(path: str | Path) -> dict[str, Any]:
    """Load a TOML file with optional [ingest] and [anomaly] tables."""
    with open(path, "rb") as fh:
        return tomllib.load(fh)


def ingest_config_from(data: dict[str, Any] | None) -> IngestConfig:
    base = IngestConfig()
    if not data:
        return base
    known = {f.name for f in fields(base)}
    unknown = set(data) - known
    if unknown:
        raise ValueError(f"unknown ingest config keys: {sorted(unknown)}")
    return replace(base, **{k: tuple(v) if isinstance(v, list) else v for k, v in data.items()})
