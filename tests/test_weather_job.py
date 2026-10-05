"""Weather job with fixture fetchers against PostGIS."""

import os
import uuid
from datetime import date, timedelta

import pytest

from canesat import db
from canesat.weather.config import RainConfig
from canesat.weather.gpm import FixtureRainFetcher
from canesat.weather.job import run_weather_check
from canesat.weather.smap import FixtureSoilFetcher

pytestmark = pytest.mark.db


@pytest.fixture
def schema():
    name = f"test_wx_{uuid.uuid4().hex[:8]}"
    url = os.environ["DATABASE_URL"]
    with db.connect(url, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        c.execute(f"CREATE SCHEMA {name}")
    with db.connect(url, options=f"-c search_path={name},public") as c:
        db.migrate(c)
    yield name
    with db.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {name} CASCADE")


def test_rain_check_creates_gap_alert(schema):
    as_of = date(2026, 9, 20)
    start = as_of - timedelta(days=19)
    precip = {start + timedelta(days=i): 0.0 for i in range(20)}
    soil = {start + timedelta(days=i): (0.10, 0) for i in range(20)}
    with db.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema},public") as conn:
        gj = {
            "type": "Polygon",
            "coordinates": [[[102.43, 16.55], [102.44, 16.55], [102.44, 16.56], [102.43, 16.55]]],
        }
        pid = db.insert_plot(conn, "ไร่ทดสอบฝน", gj, tambon_code="บ้านเม็ง")
        cfg = RainConfig(
            gap_days=14,
            require_both=True,
            sm_dry_threshold=0.15,
            lookback_days=20,
            season_months=tuple(range(1, 13)),
        )
        summary = run_weather_check(
            conn,
            rain=FixtureRainFetcher(precip),
            soil=FixtureSoilFetcher(soil),
            cfg=cfg,
            plot_ids=[pid],
            as_of=as_of,
        )
        assert summary["new_alert_ids"]
        alerts = db.list_alerts(conn, pid)
        assert any(a["type"] == "rain_gap" for a in alerts)
        n_wx = conn.execute(
            "SELECT count(*) AS n FROM weather_daily WHERE plot_id = %s", (pid,)
        ).fetchone()
        assert n_wx["n"] == 20
