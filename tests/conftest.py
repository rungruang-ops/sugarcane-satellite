import os

import pytest


def pytest_collection_modifyitems(config, items):
    skip_db = None
    if not os.environ.get("DATABASE_URL"):
        skip_db = pytest.mark.skip(reason="DATABASE_URL not set (PostGIS integration test)")
    skip_ed = None
    if not os.environ.get("EARTHDATA_TOKEN"):
        skip_ed = pytest.mark.skip(reason="EARTHDATA_TOKEN not set (live Earthdata smoke)")
    for item in items:
        if skip_db is not None and "db" in item.keywords:
            item.add_marker(skip_db)
        if skip_ed is not None and "earthdata" in item.keywords:
            item.add_marker(skip_ed)


@pytest.fixture(autouse=True)
def _isolate_line_env(monkeypatch):
    """Tests build LineSettings from explicit values; don't let a real LIFF_ID / phone key in the
    developer's shell change bot replies (e.g. 'เพิ่มแปลง' turning into a LIFF link)."""
    for name in ("LIFF_ID", "CANESAT_PHONE_KEY"):
        monkeypatch.delenv(name, raising=False)
