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


def _reg(uid, **kw):
    import math

    from shapely.geometry import Polygon

    lon, lat, side = 102.55, 16.45, 200
    dlat = side / 111_320.0
    dlon = side / (111_320.0 * math.cos(math.radians(lat)))
    reg = {
        "line_user_id": uid,
        "display_name": "พี่หน่อย",
        "name": "ไร่หลังบ้าน",
        "polygon": Polygon(
            [(lon, lat), (lon + dlon, lat), (lon + dlon, lat + dlat), (lon, lat + dlat)]
        ),
        "area_rai": 25.0,
        "cane_type": "ratoon",
        "planting_date": None,
        "last_harvest_date": date(2026, 2, 1),
        "on_behalf": False,
        "member_name": None,
        "member_phone": None,
        "consents": {"service": True, "leader_view": True, "research": False},
        "consent_version": "test",
    }
    reg.update(kw)
    return reg


def test_create_and_list_plots_self_and_on_behalf(schema):
    store = PgStore(
        os.environ["DATABASE_URL"],
        connect_kwargs={"options": f"-c search_path={schema},public"},
        phone_key="test-key",
    )
    uid = "U" + uuid.uuid4().hex
    own = store.create_plot(_reg(uid))
    assert own["owner"] == "self" and own["area_rai"] == 25.0
    member = store.create_plot(
        _reg(
            uid,
            name="ไร่นางบุญมี",
            on_behalf=True,
            member_name="นางบุญมี",
            member_phone="0812345678",
            consents={"service": True},
        )
    )
    assert member["owner"] == "member" and member["phone_stored"]

    plots = store.list_plots(uid)
    assert [p["name"] for p in plots] == ["ไร่นางบุญมี", "ไร่หลังบ้าน"]
    assert plots[0]["owner_name"] == "นางบุญมี" and plots[1]["owner_name"] is None
    assert plots[1]["geometry"]["type"] == "MultiPolygon"
    assert plots[1]["last_harvest_date"] == "2026-02-01"
    assert store.plot_names(uid)[1].endswith("— ของนางบุญมี")

    rows = _q(
        schema,
        "SELECT u.display_name, pgp_sym_decrypt(u.phone_enc, 'test-key') AS phone,"
        " p.registered_by, p.source, me.line_user_id AS registrar"
        " FROM plots p JOIN users u ON u.id = p.owner_user_id"
        " JOIN users me ON me.id = p.registered_by"
        " WHERE p.id = %s",
        (member["id"],),
    )[0]
    assert rows["phone"] == "0812345678" and rows["source"] == "liff" and rows["registrar"] == uid
    consents = _q(
        schema,
        "SELECT purpose, granted, method, assisted_by FROM consents WHERE plot_id = %s"
        " ORDER BY purpose",
        (member["id"],),
    )
    assert {c["purpose"]: c["granted"] for c in consents} == {
        "leader_view": False,
        "research": False,
        "service": False,  # pending until member confirms
    }
    assert all(c["method"] == "assisted_pending" and c["assisted_by"] for c in consents)
    # confirm_token is on the row (migration 0004)
    tok = _q(
        schema,
        "SELECT confirm_token, confirmed_at FROM consents"
        " WHERE plot_id = %s AND purpose = 'service'",
        (member["id"],),
    )[0]
    assert tok["confirm_token"] and tok["confirmed_at"] is None


def test_create_plot_without_phone_key_does_not_store_phone(schema):
    store = PgStore(
        os.environ["DATABASE_URL"], connect_kwargs={"options": f"-c search_path={schema},public"}
    )
    out = store.create_plot(
        _reg(
            "U" + uuid.uuid4().hex,
            on_behalf=True,
            member_name="นายสมัย",
            member_phone="0812345678",
        )
    )
    assert out["phone_stored"] is False
