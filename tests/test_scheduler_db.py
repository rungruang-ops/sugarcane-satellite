"""Scheduler against a real Postgres: job_runs store, advisory lock, `schedule status` CLI.

Needs DATABASE_URL (CI provides a postgis service). Only the job_runs migration is applied, in a
throw-away schema, so nothing existing is touched.
"""

import json
import os
import uuid
from datetime import datetime, timedelta

import pytest
from click.testing import CliRunner

from canesat import db
from canesat import scheduler as sch
from canesat.cli import main

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def schema():
    name = f"test_{uuid.uuid4().hex[:8]}"
    url = os.environ["DATABASE_URL"]
    sql = dict(db.migration_files())["0005_job_runs.sql"]
    with db.connect(url, autocommit=True) as c:
        c.execute(f"CREATE SCHEMA {name}")
    with db.connect(url, autocommit=True, options=f"-c search_path={name}") as c:
        c.execute(sql)
    yield name
    with db.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def dsn(schema):
    """DATABASE_URL that lands in the throw-away schema (libpq `options` URI parameter)."""
    url = os.environ["DATABASE_URL"]
    return f"{url}{'&' if '?' in url else '?'}options=-c%20search_path%3D{schema}"


@pytest.fixture
def conn(dsn):
    c = db.connect(dsn)
    c.execute("DELETE FROM job_runs")
    c.commit()
    yield c
    c.close()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in ("ENABLE_PUSH_ALERTS", "EARTHDATA_TOKEN"):
        monkeypatch.delenv(key, raising=False)


def test_start_finish_and_load_roundtrip(conn):
    store = sch.PgStore(conn)
    now = datetime.now(tz=sch.BANGKOK)
    run_id = store.start_run("s2_fetch", now)
    (running,) = store.load_runs(now)["s2_fetch"]
    assert running.status == "running" and running.finished_at is None

    store.finish_run(run_id, "ok", 0, "all good", now + timedelta(seconds=5))
    (done,) = store.load_runs(now)["s2_fetch"]
    assert (done.status, done.exit_code) == ("ok", 0)
    assert done.finished_at == now + timedelta(seconds=5) and done.started_at.tzinfo
    assert conn.execute("SELECT detail FROM job_runs").fetchone()["detail"] == "all good"


def test_load_runs_only_returns_recent_history(conn):
    store = sch.PgStore(conn)
    now = datetime.now(tz=sch.BANGKOK)
    store.start_run("s2_fetch", now - timedelta(days=sch.HISTORY_DAYS + 5))
    store.start_run("notify", now - timedelta(hours=1))
    assert set(store.load_runs(now)) == {"notify"}


def test_status_check_constraint_rejects_unknown_status(conn):
    store = sch.PgStore(conn)
    run_id = store.start_run("notify", datetime.now(tz=sch.BANGKOK))
    with pytest.raises(Exception, match="job_runs_status_check"):
        store.finish_run(run_id, "weird", 0, "", datetime.now(tz=sch.BANGKOK))
    conn.rollback()


def test_abandon_stale_marks_only_running_rows(conn):
    store = sch.PgStore(conn)
    now = datetime.now(tz=sch.BANGKOK)
    leftover = store.start_run("s2_fetch", now)
    finished = store.start_run("notify", now)
    store.finish_run(finished, "ok", 0, "", now)
    assert store.abandon_stale() == 1
    assert store.abandon_stale() == 0
    statuses = {
        r["id"]: r["status"] for r in conn.execute("SELECT id, status FROM job_runs").fetchall()
    }
    assert statuses == {leftover: "abandoned", finished: "ok"}


def test_tick_with_the_real_store_runs_once_then_waits(conn):
    store = sch.PgStore(conn)
    calls = []

    def runner(job, now):
        calls.append(job.name)
        return "ok", 0, ""

    jobs = (sch.JOBS[0], sch.JOBS[1])  # s2_fetch + ndvi_rules
    first = sch.tick(store, jobs=jobs, runner=runner)
    assert [r["action"] for r in first] == ["ran", "ran"]
    second = sch.tick(store, jobs=jobs, runner=runner)
    assert [r["action"] for r in second] == ["not_due", "not_due"]
    assert calls == ["s2_fetch", "ndvi_rules"]


def test_advisory_lock_is_exclusive_and_released(dsn):
    a, b = db.connect(dsn), db.connect(dsn)
    try:
        assert sch.try_lock(a) is True
        assert sch.try_lock(b) is False  # a second scheduler instance must back off
        sch.unlock(a)
        assert sch.try_lock(b) is True
        sch.unlock(b)
    finally:
        a.close()
        b.close()


def test_run_once_connects_with_keepalives_and_releases_the_lock(dsn, conn):
    sch.run(dsn, once=True, jobs=())  # no jobs: only the connect / lock / tick / unlock path
    other = db.connect(dsn)
    try:
        assert sch.try_lock(other) is True
        sch.unlock(other)
    finally:
        other.close()


def test_schedule_status_cli(dsn, conn, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", dsn)
    result = CliRunner().invoke(main, ["schedule", "status"])
    assert result.exit_code == 0, result.output
    states = {row["job"]: row["state"] for row in json.loads(result.output)}
    assert states["s2_fetch"] == "due"  # never ran
    assert states["ndvi_rules"] == "waiting"  # needs a successful s2_fetch first
    assert states["rain_check"].startswith("blocked")
    assert states["notify"].startswith("blocked")
