"""PgStore against PostGIS (runs in CI; skipped without DATABASE_URL)."""

import os
import uuid
from datetime import UTC, date, datetime

import pytest

from canesat import db
from canesat.line.store import PgStore

pytestmark = pytest.mark.db


@pytest.fixture(scope="module")
def schema():
    name = f"test_line_{uuid.uuid4().hex[:8]}"
    url = os.environ["DATABASE_URL"]
    with db.connect(url, autocommit=True) as c:
        c.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        c.execute(f"CREATE SCHEMA {name}")
    with db.connect(url, options=f"-c search_path={name},public") as c:
        db.migrate(c)
    yield name
    with db.connect(url, autocommit=True) as c:
        c.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture
def store(schema):
    return PgStore(
        os.environ["DATABASE_URL"], connect_kwargs={"options": f"-c search_path={schema},public"}
    )


def _q(schema, sql, params=()):
    with db.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema},public") as c:
        return list(c.execute(sql, params))


def test_follow_unfollow_refollow(store, schema):
    uid = "U" + uuid.uuid4().hex
    t1 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
    assert store.upsert_follow(uid, "สมาน", t1)
    row = _q(schema, "SELECT * FROM users WHERE line_user_id = %s", (uid,))[0]
    assert row["display_name"] == "สมาน" and row["is_active"] and row["followed_at"] == t1

    assert store.mark_unfollow(uid, datetime(2026, 10, 6, tzinfo=UTC))
    row = _q(schema, "SELECT * FROM users WHERE line_user_id = %s", (uid,))[0]
    assert not row["is_active"] and row["unfollowed_at"] is not None

    # re-follow without a profile name keeps the old name
    assert store.upsert_follow(uid, None, datetime(2026, 10, 7, tzinfo=UTC))
    row = _q(schema, "SELECT * FROM users WHERE line_user_id = %s", (uid,))[0]
    assert row["display_name"] == "สมาน" and row["is_active"] and row["unfollowed_at"] is None


def test_unfollow_unknown_user_is_noop(store):
    assert store.mark_unfollow("Unobody", datetime.now(tz=UTC)) is False


def test_feedback_recorded_on_alert(store, schema):
    uid = "U" + uuid.uuid4().hex
    store.upsert_follow(uid, "บุญมี", datetime.now(tz=UTC))
    with db.connect(os.environ["DATABASE_URL"], options=f"-c search_path={schema},public") as c:
        gj = {
            "type": "Polygon",
            "coordinates": [[[102.43, 16.55], [102.44, 16.55], [102.44, 16.56], [102.43, 16.55]]],
        }
        plot_id = db.insert_plot(c, "ไร่หลังบ้าน", gj)
        c.execute(
            "UPDATE plots SET owner_user_id = (SELECT id FROM users WHERE line_user_id = %s)"
            " WHERE id = %s",
            (uid, plot_id),
        )
        alert_id = db.insert_alert(
            c, plot_id, "greenness", "red", date(2026, 9, 29), -2.5, -0.2, {}
        )
        c.commit()

    assert store.record_feedback(alert_id, "borer", uid, datetime.now(tz=UTC)) == "recorded"
    row = _q(schema, "SELECT * FROM alerts WHERE id = %s", (alert_id,))[0]
    assert row["feedback"] == "borer" and row["feedback_user_id"] is not None
    assert store.record_feedback(999999, "weed", uid, datetime.now(tz=UTC)) == "not_found"
    names = store.plot_names(uid)
    assert len(names) == 1 and names[0].startswith("ไร่หลังบ้าน (ประมาณ ")
