import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set (PostGIS integration test)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _isolate_line_env(monkeypatch):
    """Tests build LineSettings from explicit values; don't let a real LIFF_ID / phone key in the
    developer's shell change bot replies (e.g. 'เพิ่มแปลง' turning into a LIFF link)."""
    for name in ("LIFF_ID", "CANESAT_PHONE_KEY"):
        monkeypatch.delenv(name, raising=False)
