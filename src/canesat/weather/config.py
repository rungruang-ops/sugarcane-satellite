"""Tunable rain-gap / drought thresholds (design §5.2). Starting points for agronomist review."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from typing import Any


@dataclass(frozen=True)
class RainConfig:
    dry_day_mm: float = 1.0  # daily precip below this = a "dry" day
    gap_days: int = 14  # consecutive dry days → rain_gap candidate
    rain_back_3d_mm: float = 20.0  # 3-day accum after a gap → rain_back
    sm_dry_threshold: float = 0.15  # SMAP volumetric water content (m³/m³) below = dry
    require_both: bool = True  # rain gap AND low soil moisture (design §5.2)
    cooldown_days: int = 14  # max one rain_gap alert per plot per N days
    lookback_days: int = 40  # how many days of weather to fetch/evaluate
    # young cane needs water most; skip rain alerts only for very young canopy if desired
    min_crop_age_days: int = 0  # 0 = alert regardless of crop age (rain is area-level)
    season_months: tuple[int, ...] = (5, 6, 7, 8, 9, 10, 11)  # rainy season focus (Thai)

    @classmethod
    def from_mapping(cls, data: dict[str, Any] | None) -> RainConfig:
        return cls().merged(data or {})

    def merged(self, data: dict[str, Any]) -> RainConfig:
        known = {f.name for f in fields(self)}
        unknown = set(data) - known
        if unknown:
            raise ValueError(f"unknown rain config keys: {sorted(unknown)}")
        clean = {k: tuple(v) if isinstance(v, list) else v for k, v in data.items()}
        return replace(self, **clean)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
