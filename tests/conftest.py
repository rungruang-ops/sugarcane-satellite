import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("DATABASE_URL"):
        return
    skip = pytest.mark.skip(reason="DATABASE_URL not set (PostGIS integration test)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)
