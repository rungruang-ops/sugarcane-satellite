import numpy as np
import pytest

from canesat.ndvi import (
    boa_offset,
    clear_mask,
    compute_ndvi,
    dilate,
    likely_cane_mask,
    median_mad,
    zonal_stats,
)


@pytest.mark.parametrize(
    ("baseline", "expected"),
    [
        ("05.11", 1000),
        ("04.00", 1000),
        ("03.01", 0),
        (None, 0),
        ("", 0),
        ("garbage", 0),
        (5.1, 1000),
    ],
)
def test_boa_offset(baseline, expected):
    assert boa_offset(baseline) == expected


def test_ndvi_with_offset_matches_reflectance_formula():
    # reflectance red=0.05, nir=0.35 -> NDVI 0.75; DN includes +1000 offset
    red = np.array([[1500]], dtype=np.uint16)
    nir = np.array([[4500]], dtype=np.uint16)
    assert compute_ndvi(red, nir, 1000)[0, 0] == pytest.approx(0.75)
    # forgetting the offset biases NDVI low — this is why baseline >= 4 matters
    assert compute_ndvi(red, nir, 0)[0, 0] == pytest.approx(0.5)


def test_ndvi_invalid_pixels_are_nan():
    red = np.array([[0, 900, 2000]], dtype=np.uint16)  # nodata, below offset, ok
    nir = np.array([[3000, 3000, 2000]], dtype=np.uint16)
    out = compute_ndvi(red, nir, 1000)
    assert np.isnan(out[0, 0]) and np.isnan(out[0, 1])
    assert out[0, 2] == pytest.approx(0.0)


def test_dilate_grows_8_connected():
    m = np.zeros((5, 5), bool)
    m[2, 2] = True
    d1 = dilate(m, 1)
    assert d1.sum() == 9 and d1[1:4, 1:4].all()
    assert dilate(m, 2).sum() == 25
    assert dilate(m, 0).sum() == 1


def test_clear_mask_keeps_only_veg_and_bare_and_buffers_clouds():
    scl = np.full((5, 5), 4, dtype=np.uint8)
    scl[0, :] = 5  # bare soil: clear
    scl[4, 4] = 9  # cloud high probability
    scl[0, 0] = 6  # water: not kept
    m = clear_mask(scl, dilate_px=1)
    assert not m[0, 0]
    assert not m[4, 4] and not m[3, 3] and not m[3, 4] and not m[4, 3]
    assert m[2, 2] and m[0, 1]
    assert m.sum() == 25 - 1 - 4
    assert clear_mask(scl, dilate_px=0).sum() == 25 - 2


def test_median_mad():
    med, mad = median_mad(np.array([0.1, 0.2, 0.3, np.nan, 10.0]))
    assert med == pytest.approx(0.25)
    assert mad == pytest.approx(0.1)
    assert np.isnan(median_mad(np.array([np.nan]))[0])


def test_zonal_stats_clear_fraction_threshold():
    ndvi = np.full((10, 10), 0.7)
    zone = np.zeros((10, 10), bool)
    zone[:, :5] = True  # 50 px
    clear = np.ones((10, 10), bool)
    clear[:, :2] = False  # 20 of the 50 zone px are cloudy
    st = zonal_stats(ndvi, clear, zone, min_clear_fraction=0.6)
    assert (st.n_px, st.n_clear_px) == (50, 30)
    assert st.clear_fraction == pytest.approx(0.6)
    assert st.is_clear and st.median == pytest.approx(0.7)
    assert not zonal_stats(ndvi, clear, zone, min_clear_fraction=0.61).is_clear


def test_zonal_stats_min_pixels_and_empty():
    ndvi = np.full((3, 3), 0.5)
    zone = np.zeros((3, 3), bool)
    zone[0, :3] = True
    st = zonal_stats(ndvi, np.ones((3, 3), bool), zone, 0.6, min_clear_px=4)
    assert st.clear_fraction == 1 and not st.is_clear
    none = zonal_stats(ndvi, np.zeros((3, 3), bool), zone)
    assert none.median is None and none.n_clear_px == 0 and not none.is_clear


def test_zonal_stats_median_is_robust_to_outliers():
    ndvi = np.full((10, 10), 0.75)
    ndvi[0, :10] = 0.05  # a road along the edge
    st = zonal_stats(ndvi, np.ones_like(ndvi, bool), np.ones_like(ndvi, bool))
    assert st.median == pytest.approx(0.75)


def test_likely_cane_mask():
    wc = np.array([[40, 40, 10, 40]])
    ndvi = np.array([[0.7, 0.5, 0.8, 0.9]])
    clear = np.array([[True, True, True, False]])
    assert likely_cane_mask(wc, ndvi, clear).tolist() == [[True, False, False, False]]
