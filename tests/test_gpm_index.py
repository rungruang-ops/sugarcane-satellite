from canesat.weather.gpm import lon_lat_index
from canesat.weather.smap import ease_row_col


def test_imerg_index_khon_kaen():
    lon_i, lat_i = lon_lat_index(102.45, 16.55)
    assert lon_i == 2824
    assert lat_i == 1065


def test_smap_ease_row_col_khon_kaen():
    row, col = ease_row_col(16.55, 102.45)
    assert row == 580
    assert col == 3025
