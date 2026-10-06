from datetime import UTC, datetime

from shapely.geometry import box

from canesat.stac import SceneRef, select_scenes

WINDOW = box(102.40, 16.53, 102.47, 16.59)
BIG = box(101.0, 15.0, 104.0, 18.0)
EDGE = box(102.45, 15.0, 104.0, 18.0)  # does not contain the window
H = {"B04": "r", "B08": "n", "SCL": "s"}


def s(item_id, day, epsg=32648, gen="2025-01-01", cloud=10.0, fp=BIG, hrefs=H):
    return SceneRef(
        item_id,
        datetime(2025, 1, day, 3, 40, tzinfo=UTC),
        "48QTD",
        epsg,
        cloud,
        "05.11",
        gen,
        fp,
        hrefs,
    )


def test_keeps_newest_reprocessing():
    old = s("old", 3, gen="2025-01-03T09:00")
    new = s("new", 3, gen="2025-11-21T07:12")
    assert [x.item_id for x in select_scenes([old, new], WINDOW, 32648)] == ["new"]


def test_prefers_own_utm_zone_tile():
    z48 = s("T48QTD", 3, epsg=32648)
    z47 = s("T47QRU", 3, epsg=32647, cloud=0.1)
    assert [x.item_id for x in select_scenes([z47, z48], WINDOW, 32648)] == ["T48QTD"]


def test_prefers_footprint_containing_window():
    partial = s("partial", 3, fp=EDGE, gen="2026")
    full = s("full", 3, epsg=32647)
    assert [x.item_id for x in select_scenes([partial, full], WINDOW, 32648)] == ["full"]


def test_one_per_date_sorted_and_skips_missing_assets():
    out = select_scenes([s("b", 8), s("a", 3), s("x", 5, hrefs={"B04": "r"})], WINDOW, 32648)
    assert [x.item_id for x in out] == ["a", "b"]
