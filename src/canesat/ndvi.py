"""Pure raster math: reflectance offset, NDVI, SCL cloud mask and zonal statistics.

Everything here works on numpy arrays only, so it is unit-testable without network access.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import numpy as np

# MAD -> sigma for a normal distribution
MAD_TO_SIGMA = 1.4826


def boa_offset(processing_baseline: str | float | None) -> int:
    """Digital-number offset to subtract from Sentinel-2 L2A bands.

    Since processing baseline 04.00 (Jan 2022) L2A products carry BOA_ADD_OFFSET = -1000,
    i.e. reflectance = (DN - 1000) / 10000. Older products have no offset. Removing it keeps
    NDVI comparable across years (design §5.1 step 3).
    """
    if processing_baseline in (None, ""):
        return 0
    try:
        return 1000 if float(processing_baseline) >= 4.0 else 0
    except (TypeError, ValueError):
        return 0


def compute_ndvi(red_dn: np.ndarray, nir_dn: np.ndarray, offset: int = 0) -> np.ndarray:
    """NDVI = (NIR - Red) / (NIR + Red) from raw L2A digital numbers.

    Pixels with nodata (DN 0), non-positive reflectance after offset removal or a zero
    denominator become NaN.
    """
    red = red_dn.astype("float64")
    nir = nir_dn.astype("float64")
    nodata = (red_dn == 0) | (nir_dn == 0)
    red = red - offset
    nir = nir - offset
    bad = nodata | (red <= 0) | (nir <= 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        ndvi = (nir - red) / (nir + red)
    ndvi[bad | ~np.isfinite(ndvi)] = np.nan
    return ndvi


def dilate(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    """Binary dilation with a 3x3 (8-connected) structuring element, numpy only."""
    out = mask.astype(bool).copy()
    for _ in range(max(0, iterations)):
        padded = np.pad(out, 1, mode="constant", constant_values=False)
        grown = np.zeros_like(out)
        h, w = out.shape
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                grown |= padded[dy : dy + h, dx : dx + w]
        out = grown
    return out


def clear_mask(
    scl: np.ndarray,
    clear_classes: Iterable[int] = (4, 5),
    cloud_classes: Iterable[int] = (3, 8, 9, 10),
    dilate_px: int = 1,
) -> np.ndarray:
    """True where the SCL class is 'clear' (vegetation / not-vegetated by default) and the
    pixel is not within ``dilate_px`` pixels of a cloud or cloud-shadow pixel."""
    keep = np.isin(scl, list(clear_classes))
    if dilate_px > 0:
        cloudy = np.isin(scl, list(cloud_classes))
        keep &= ~dilate(cloudy, dilate_px)
    return keep


def median_mad(values: np.ndarray) -> tuple[float, float]:
    """Median and (unscaled) median absolute deviation of finite values."""
    v = np.asarray(values, dtype="float64")
    v = v[np.isfinite(v)]
    if v.size == 0:
        return float("nan"), float("nan")
    med = float(np.median(v))
    return med, float(np.median(np.abs(v - med)))


@dataclass(frozen=True)
class ZoneStats:
    n_px: int
    n_clear_px: int
    clear_fraction: float
    median: float | None
    p10: float | None
    p90: float | None
    mad: float | None
    is_clear: bool


def zonal_stats(
    ndvi: np.ndarray,
    clear: np.ndarray,
    zone: np.ndarray,
    min_clear_fraction: float = 0.6,
    min_clear_px: int = 4,
) -> ZoneStats:
    """Statistics of NDVI over ``zone`` pixels that are clear and finite.

    ``clear_fraction`` = usable pixels / zone pixels. ``is_clear`` is True when the
    observation passes both the fraction and the minimum-pixel thresholds.
    """
    zone = zone.astype(bool)
    n_px = int(zone.sum())
    usable = zone & clear.astype(bool) & np.isfinite(ndvi)
    n_clear = int(usable.sum())
    frac = n_clear / n_px if n_px else 0.0
    if n_clear == 0:
        return ZoneStats(n_px, 0, frac, None, None, None, None, False)
    vals = ndvi[usable]
    med, mad = median_mad(vals)
    p10, p90 = (float(x) for x in np.percentile(vals, [10, 90]))
    ok = frac >= min_clear_fraction and n_clear >= min_clear_px
    return ZoneStats(n_px, n_clear, frac, med, p10, p90, mad, ok)


def likely_cane_mask(
    worldcover: np.ndarray,
    ref_ndvi: np.ndarray,
    ref_clear: np.ndarray,
    ndvi_threshold: float = 0.6,
    cropland_class: int = 40,
) -> np.ndarray:
    """'Probably sugarcane' pixels (same definition as the Khon Kaen exploratory chart):
    WorldCover cropland AND NDVI > threshold on an early-December scene (after rice harvest,
    before cane crushing). This is an approximation, not a verified cane map."""
    with np.errstate(invalid="ignore"):
        green = ref_ndvi > ndvi_threshold
    return (worldcover == cropland_class) & green & ref_clear.astype(bool)
