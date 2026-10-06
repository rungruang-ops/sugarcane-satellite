"""PostGIS integration tests. Run with DATABASE_URL set (CI provides a postgis service).

Each test session works in a throw-away schema, so it never touches existing tables.
"""

import json
import os
import uuid
from datetime import date, timedelta

import pytest
from click.testing import CliRunner
from shapely.geometry import mapping

from canesat import db
from canesat.config import AnomalyConfig
from canesat.detect import run_detect
from canesat.geometry import square_around
from canesat.ingest import Observation

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def schema():
    name = f"test_{uuid.uuid4().hex[:8]}"
    url = os.environ["DATABASE_URL"]
    with db.connect(url, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        c.execute(f"CREATE SCHEMA {name}")
    yield name
    with db.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def conn(schema):
    c = db.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema},public")
    yield c
    c.close()


def _obs(d, ndvi, i, clear=True):
    return Observation(
        item_id=f"S2X_TEST_{d:%Y%m%d}_{i}",
        obs_date=d,
        acquired_at=f"{d.isoformat()}T03:40:00+00:00",
        mgrs_tile="48QTD",
        cloud_cover=5.0,
        processing_baseline="05.11",
        median_ndvi=ndvi,
        p10_ndvi=ndvi - 0.05,
        p90_ndvi=ndvi + 0.05,
        n_px=9604,
        n_clear_px=9604 if clear else 1000,
        clear_fraction=1.0 if clear else 0.1,
        is_clear=clear,
        flags=[] if clear else ["low_clear_fraction"],
        ring_median=0.75,
        ring_mad=0.03,
        ring_n_px=40000,
        ring_clear_fraction=1.0,
    )


def test_migrate_insert_ingest_detect(conn, schema, monkeypatch, tmp_path):
    assert db.migrate(conn) == ["0001_init.sql"]
    assert db.migrate(conn) == []  # idempotent

    geom = mapping(square_around(16.5589, 102.4372, 1000))
    pid = db.insert_plot(
        conn,
        "Nong Ruea box",
        geom,
        tambon_code="400501",
        cane_type="ratoon",
        last_harvest_date=date(2026, 1, 15),
        ratoon_no=1,
    )
    other = db.insert_plot(conn, "Neighbour", mapping(square_around(16.57, 102.44, 300)))
    p = db.get_plot(conn, pid)
    assert float(p["area_rai"]) == pytest.approx(625, rel=0.01)  # 1 km² = 625 rai
    assert {r["id"] for r in db.list_plots(conn)} == {pid, other}
    assert [q for q, _ in db.plot_distances(conn, pid, 5000)] == [other]

    d0 = date(2026, 6, 1)
    values = [0.74, 0.73, 0.60, 0.58, 0.57, 0.75]
    obs = [_obs(d0 + timedelta(days=5 * i), v, i) for i, v in enumerate(values)]
    obs.append(_obs(d0 + timedelta(days=2), 0.2, 99, clear=False))
    assert db.upsert_observations(conn, pid, obs) == 7
    assert db.upsert_observations(conn, pid, obs[:1]) == 1  # upsert, no duplicate
    assert len(db.ingested_item_ids(conn, pid)) == 7
    # committed: visible from a separate connection (batch ingest persists per plot)
    with db.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema},public") as c2:
        assert len(db.ingested_item_ids(c2, pid)) == 7

    summary = run_detect(conn, [pid], AnomalyConfig())
    s = summary["plots"][pid]
    assert s["n_clear_obs"] == 6 and len(s["new_alert_ids"]) == 1
    alerts = db.list_alerts(conn, pid)
    assert len(alerts) == 1
    a = alerts[0]
    assert a["type"] == "greenness" and a["obs_date"] == d0 + timedelta(days=15)
    assert a["status"] == "pending" and a["z_score"] <= -2
    assert a["details"]["nb_source"] == "cane_pixels"

    # rerun: no duplicate alerts, statuses stable
    again = run_detect(conn, [pid], AnomalyConfig())
    assert again["plots"][pid]["new_alert_ids"] == []
    rows = db.fetch_observations(conn, [pid], clear_only=True)
    assert [r["status"] for r in rows] == [
        "normal",
        "normal",
        "watch",
        "alert",
        "alert_cooldown",
        "normal",
    ]

    # thresholds from the settings table
    db.set_setting(conn, "anomaly", {"cooldown_days": 30})
    assert db.get_setting(conn, "anomaly") == {"cooldown_days": 30}

    # CLI export reads the same schema through PGOPTIONS
    from canesat.cli import main

    monkeypatch.setenv("PGOPTIONS", f"-c search_path={schema},public")
    out = tmp_path / "x.json"
    r = CliRunner().invoke(main, ["export", "--plot-id", str(pid), "--out", str(out)])
    assert r.exit_code == 0, r.output
    data = json.loads(out.read_text())
    assert len(data["observations"]) == 7 and len(data["alerts"]) == 1
    r = CliRunner().invoke(
        main, ["export", "--plot-id", str(pid), "--out", str(tmp_path / "x.csv")]
    )
    assert r.exit_code == 0 and (tmp_path / "x.csv").read_text().count("\n") == 8
