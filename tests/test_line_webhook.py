"""LINE webhook: signature verification and event routing (FastAPI TestClient, fakes)."""

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from canesat.line.app import create_app
from canesat.line.config import LineSettings
from canesat.line.handlers import match_keyword
from canesat.line.store import NullStore, PgStore
from line_fakes import (
    TEST_SECRET,
    USER_ID,
    FakeLine,
    FakeStore,
    follow_event,
    payload,
    postback_event,
    sign,
    sticker_event,
    text_event,
    unfollow_event,
)


@pytest.fixture
def env():
    line, store = FakeLine(), FakeStore()
    app = create_app(
        LineSettings(channel_secret=TEST_SECRET, channel_access_token="x", database_url=None),
        line_api=line,
        store=store,
    )
    return TestClient(app), line, store


def post(client, body, signature=None):
    headers = {"Content-Type": "application/json"}
    if signature is not False:
        headers["X-Line-Signature"] = signature if signature is not None else sign(body)
    return client.post("/callback", content=body.encode("utf-8"), headers=headers)


def reply_text(line, i=-1):
    return line.replies[i][1][0]["text"]


# ------------------------------------------------------------------ signature
def test_valid_signature_accepted(env):
    client, _, _ = env
    r = post(client, payload())  # LINE "verify" request: no events
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_invalid_signature_rejected(env):
    client, line, store = env
    body = payload(text_event("เมนู"))
    r = post(client, body, signature=sign(body, secret="f" * 32))
    assert r.status_code == 400
    assert line.replies == [] and store.calls == []


def test_tampered_body_rejected(env):
    client, line, _ = env
    body = payload(text_event("เมนู"))
    r = post(client, body.replace("เมนู", "ฝน"), signature=sign(body))
    assert r.status_code == 400
    assert line.replies == []


def test_missing_signature_rejected(env):
    client, _, _ = env
    assert post(client, payload(), signature=False).status_code == 400


def test_healthz(env):
    client, _, _ = env
    assert client.get("/healthz").json() == {"status": "ok", "db": "fake"}


def test_create_app_requires_secret():
    with pytest.raises(RuntimeError):
        create_app(LineSettings(channel_secret=None, channel_access_token="x"))


def test_settings_repr_masks_secrets():
    s = LineSettings(channel_secret="topsecret", channel_access_token="tok" * 20)
    assert "topsecret" not in repr(s) and "toktok" not in repr(s)


# ------------------------------------------------------------------ routing
def test_follow_replies_welcome_and_stores_user(env):
    client, line, store = env
    assert post(client, payload(follow_event())).status_code == 200
    token, msgs = line.replies[0]
    assert token == "rt-follow"
    t = msgs[0]["text"]
    assert "เบิ่งไฮ่" in t and "สมาน" in t
    assert "เขียวน้อยกว่า" in t and "ฝนทิ้งช่วง" in t and "ปีที่แล้ว" in t
    assert "ลงทะเบียนแปลง" in t and "เร็ว ๆ นี้" in t
    assert store.calls[0][:3] == ("follow", USER_ID, "สมาน")


def test_unfollow_marks_inactive_without_reply(env):
    client, line, store = env
    post(client, payload(unfollow_event()))
    assert line.replies == []
    assert store.calls[0][:2] == ("unfollow", USER_ID)


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("เมนู", "ทำอะไรได้บ้าง"),
        ("help", "ทำอะไรได้บ้าง"),
        ("ฝน", "ฝน / ความชื้นดิน"),
        ("ฝนจะตกไหม", "ฝน / ความชื้นดิน"),
        ("เทียบเพื่อนบ้าน", "เทียบเพื่อนบ้าน & ปีที่แล้ว"),
        ("เพิ่มแปลง", "การลงทะเบียนแปลงกำลังจะเปิด"),
        ("หัวหน้ากลุ่ม", "เมนูหัวหน้ากลุ่ม"),
        ("อะไรนะ", "ยังตอบได้แค่บางคำ"),
    ],
)
def test_text_keywords(env, text, needle):
    client, line, _ = env
    post(client, payload(text_event(text)))
    assert needle in reply_text(line)
    assert line.replies[-1][1][0]["quickReply"]["items"]


def test_my_plots_without_and_with_plots():
    line = FakeLine()
    for plots, needle in [(None, "ยังไม่มีแปลง"), (["ไร่หลังบ้าน (ประมาณ 24 ไร่)"], "ไร่หลังบ้าน")]:
        app = create_app(
            LineSettings(channel_secret=TEST_SECRET, channel_access_token="x"),
            line_api=line,
            store=FakeStore(plots=plots),
        )
        post(TestClient(app), payload(text_event("แปลงของฉัน")))
        assert needle in reply_text(line)


def test_non_text_message(env):
    client, line, _ = env
    post(client, payload(sticker_event()))
    assert "เฉพาะข้อความ" in reply_text(line)


def test_feedback_postback_records_and_thanks(env):
    client, line, store = env
    post(client, payload(postback_event("action=feedback&alert_id=42&answer=water_stress")))
    assert store.calls[0] == ("feedback", 42, "water_stress", USER_ID)
    assert "ขาดน้ำ" in reply_text(line)


def test_feedback_postback_rejects_unknown_answer(env):
    client, line, store = env
    post(client, payload(postback_event("action=feedback&alert_id=42&answer=aliens")))
    assert store.calls == []
    assert "ยังตอบได้แค่บางคำ" in reply_text(line)


def test_rain_report_postback(env):
    client, line, _ = env
    post(client, payload(postback_event("action=rain_report&answer=little")))
    assert "ตกนิดหน่อย" in reply_text(line)


def test_multiple_events_in_one_delivery(env):
    client, line, store = env
    post(client, payload(follow_event(), text_event("เมนู"), unfollow_event()))
    assert [t for t, _ in line.replies] == ["rt-follow", "rt-text"]
    assert [c[0] for c in store.calls] == ["follow", "unfollow"]


def test_db_unavailable_degrades_gracefully():
    line = FakeLine(display_name=None)
    app = create_app(
        LineSettings(channel_secret=TEST_SECRET, channel_access_token="x"),
        line_api=line,
        store=NullStore(),
    )
    client = TestClient(app)
    post(client, payload(follow_event(), postback_event("action=feedback&alert_id=1&answer=weed")))
    assert len(line.replies) == 2
    assert "สวัสดีครับ 🙏" in reply_text(line, 0)
    assert client.get("/healthz").json()["db"] == "disabled"


def test_handler_error_does_not_break_batch(env):
    client, line, store = env

    def boom(*a, **k):
        raise RuntimeError("db exploded")

    store.upsert_follow = boom
    r = post(client, payload(follow_event(), text_event("ฝน")))
    assert r.status_code == 200
    assert [t for t, _ in line.replies] == ["rt-text"]


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("  เมนู ", "help"),
        ("HELP", "help"),
        ("วิธีใช้/ติดต่อ", "help"),
        ("แปลงของฉัน", "my_plots"),
        ("ฝน/ความชื้นดิน", "rain"),
        ("หัวหน้ากลุ่ม", "leader"),
        ("", None),
        ("ok", None),
    ],
)
def test_match_keyword(text, intent):
    assert match_keyword(text) == intent


def test_unreachable_db_degrades():
    s = PgStore("postgresql://x:y@127.0.0.1:1/none", connect_timeout=1)
    now = datetime.now(tz=UTC)
    assert s.upsert_follow("U1", "a", now) is False
    assert s.record_feedback(1, "weed", "U1", now) == "unavailable"
    assert s.plot_names("U1") is None
    assert s.status() == "unavailable"
