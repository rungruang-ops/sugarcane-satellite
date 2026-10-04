"""LIFF page + /api/plots (fake verifier/store), keyword links, rich menu, ingest queue."""

import json
import threading

import pytest
from fastapi.testclient import TestClient

from canesat.line.app import create_app
from canesat.line.config import LineSettings
from canesat.line.idtoken import InvalidIdToken, LineIdentity
from canesat.line.ingest_queue import IngestQueue
from canesat.line.richmenu import HEIGHT, WIDTH, rich_menu_spec
from canesat.line.store import StoreUnavailable
from line_fakes import TEST_SECRET, FakeLine, FakeStore, payload, sign, text_event
from test_line_plots import square

UID = "U" + "c" * 32


class FakeVerifier:
    def verify(self, token):
        if token != "good":
            raise InvalidIdToken("bad")
        return LineIdentity(UID, "พี่หน่อย")


class PlotStore(FakeStore):
    def __init__(self, down=False):
        super().__init__()
        self.created = []
        self.down = down

    def create_plot(self, reg):
        if self.down:
            raise StoreUnavailable()
        self.created.append(reg)
        return {"id": 7, "name": reg["name"], "area_rai": reg["area_rai"], "owner": "self"}

    def list_plots(self, uid):
        if self.down:
            raise StoreUnavailable()
        return [{"id": 7, "name": "ไร่หลังบ้าน", "area_rai": 25.0, "geometry": square(200)}]


class FakeQueue:
    def __init__(self):
        self.ids = []

    def enqueue(self, plot_id):
        self.ids.append(plot_id)


def make(liff_id="2011859249-AbCdEfGh", store=None, queue=None):
    store = store or PlotStore()
    queue = queue if queue is not None else FakeQueue()
    line = FakeLine()
    app = create_app(
        LineSettings(channel_secret=TEST_SECRET, channel_access_token="x", liff_id=liff_id),
        line_api=line,
        store=store,
        verifier=FakeVerifier(),
        ingest=queue,
    )
    return TestClient(app), store, queue, line


GOOD = {"Authorization": "Bearer good"}


def body(**kw):
    b = {
        "name": "ไร่หลังบ้าน",
        "cane_type": "plant",
        "ref_date": "2026-01",
        "geometry": square(200),
        "consents": {"service": True, "leader_view": False, "research": True},
    }
    b.update(kw)
    return b


# ------------------------------------------------------------------ page
def test_register_page_injects_liff_id():
    client, *_ = make()
    r = client.get("/liff/register")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert r.headers["cache-control"] == "no-store"
    cfg_txt = r.text.split('id="canesat-config" type="application/json">')[1].split("</script>")[0]
    cfg = json.loads(cfg_txt)
    assert cfg["liffId"] == "2011859249-AbCdEfGh"
    assert cfg["minRai"] == 0.5 and cfg["maxRai"] == 2000
    for needle in ("maplibre-gl", "static.line-scdn.net/liff", "server.arcgisonline.com", "©"):
        assert needle in r.text or needle in client.get("/liff/static/register.js").text
    assert "จำเป็น" in r.text and "อ้อยตอ" in r.text and "ลงทะเบียนแทนสมาชิก" in r.text


def test_register_page_escapes_config():
    client, *_ = make(liff_id="</script><script>alert(1)</script>")
    r = client.get("/liff/register")
    assert "<script>alert(1)" not in r.text
    assert "\\u003c/script\\u003e" in r.text


def test_register_page_without_liff_id():
    client, *_ = make(liff_id=None)
    assert '"liffId": ""' in client.get("/liff/register").text


def test_static_assets_served():
    client, *_ = make()
    assert client.get("/liff/static/register.js").status_code == 200
    assert client.get("/liff/static/register.css").status_code == 200
    assert client.get("/liff/static/../app.py").status_code == 404


# ------------------------------------------------------------------ API
def test_post_plot_requires_id_token():
    client, store, queue, _ = make()
    assert client.post("/api/plots", json=body()).status_code == 401
    r = client.post("/api/plots", json=body(), headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert store.created == [] and queue.ids == []


def test_post_plot_ok_and_ingest_queued():
    client, store, queue, _ = make()
    r = client.post("/api/plots", json=body(), headers=GOOD)
    assert r.status_code == 201, r.text
    assert r.json() == {
        "id": 7,
        "name": "ไร่หลังบ้าน",
        "area_rai": pytest.approx(25.0, rel=0.01),
        "owner": "self",
        "ingest": "queued",
    }
    reg = store.created[0]
    assert reg["line_user_id"] == UID and reg["display_name"] == "พี่หน่อย"
    assert reg["consents"] == {"service": True, "leader_view": False, "research": True}
    assert reg["planting_date"].isoformat() == "2026-01-01"
    assert reg["polygon"].geom_type == "Polygon"
    assert queue.ids == [7]


def test_post_plot_on_behalf():
    client, store, *_ = make()
    r = client.post(
        "/api/plots",
        json=body(
            on_behalf=True,
            member_name="นางบุญมี",
            member_phone="081-234-5678",
            assisted_consent_confirmed=True,
        ),
        headers=GOOD,
    )
    assert r.status_code == 201
    reg = store.created[0]
    assert reg["on_behalf"] and reg["member_name"] == "นางบุญมี"
    assert reg["member_phone"] == "0812345678"


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"geometry": square(20)}, "เล็กเกินไป"),
        ({"consents": {"service": False}}, "จำเป็น"),
        ({"name": "   "}, "ชื่อแปลง"),
    ],
)
def test_post_plot_validation_errors_in_thai(kw, msg):
    client, store, queue, _ = make()
    r = client.post("/api/plots", json=body(**kw), headers=GOOD)
    assert r.status_code == 422 and msg in r.json()["detail"]
    assert store.created == [] and queue.ids == []


def test_post_plot_schema_error():
    client, *_ = make()
    r = client.post("/api/plots", json={"name": "x"}, headers=GOOD)
    assert r.status_code == 422


def test_db_down_returns_503():
    client, *_ = make(store=PlotStore(down=True))
    assert client.post("/api/plots", json=body(), headers=GOOD).status_code == 503
    assert client.get("/api/plots", headers=GOOD).status_code == 503


def test_get_my_plots():
    client, *_ = make()
    assert client.get("/api/plots").status_code == 401
    r = client.get("/api/plots", headers=GOOD)
    assert r.status_code == 200 and r.json()["plots"][0]["name"] == "ไร่หลังบ้าน"


# ------------------------------------------------------------------ webhook links
def _say(client, text):
    b = payload(text_event(text))
    client.post("/callback", content=b.encode(), headers={"X-Line-Signature": sign(b)})


def test_add_plot_keyword_links_to_liff():
    client, _, _, line = make()
    _say(client, "เพิ่มแปลง")
    msg = line.replies[-1][1][0]
    assert msg["type"] == "flex"
    uri = msg["contents"]["footer"]["contents"][0]["action"]["uri"]
    assert uri == "https://liff.line.me/2011859249-AbCdEfGh"


def test_add_plot_keyword_without_liff_explains():
    client, _, _, line = make(liff_id=None)
    _say(client, "เพิ่มแปลง")
    assert "กำลังจะเปิด" in line.replies[-1][1][0]["text"]


def test_my_plots_without_plots_offers_liff():
    client, _, _, line = make()
    _say(client, "แปลงของฉัน")
    assert line.replies[-1][1][0]["type"] == "flex"


def test_liff_confirmation_message_gets_ack_not_keyword_reply():
    client, _, _, line = make()
    _say(client, '✅ ลงทะเบียนแปลง "ไร่หลังบ้าน" (≈ 25 ไร่) แล้ว')
    assert "ได้รับแล้ว" in line.replies[-1][1][0]["text"]


# ------------------------------------------------------------------ rich menu
def test_rich_menu_spec_liff_and_message_actions():
    plain = rich_menu_spec()
    assert plain["areas"][0]["action"] == {"type": "message", "label": "เพิ่มแปลง", "text": "เพิ่มแปลง"}
    liff = rich_menu_spec("2011859249-AbCdEfGh")
    assert liff["areas"][0]["action"]["uri"] == "https://liff.line.me/2011859249-AbCdEfGh"
    assert [a["action"]["type"] for a in liff["areas"][1:]] == ["message"] * 5
    assert liff["name"].startswith("bernghai-main") and plain["name"] != liff["name"]


def test_rich_menu_image(tmp_path):
    pytest.importorskip("PIL")
    from PIL import Image

    from canesat.line.richmenu import render_rich_menu_image

    p = render_rich_menu_image(tmp_path / "rm.png")
    with Image.open(p) as im:
        assert im.size == (WIDTH, HEIGHT)
    assert p.stat().st_size < 1_000_000


# ------------------------------------------------------------------ ingest queue
def test_ingest_queue_runs_jobs_in_background_and_survives_errors():
    seen, started = [], threading.Event()

    def job(pid):
        started.set()
        if pid == 1:
            raise RuntimeError("boom")
        seen.append(pid)
        return {"plot_id": pid}

    q = IngestQueue(job)
    q.enqueue(1)
    q.enqueue(2)
    q.join()
    assert started.is_set() and seen == [2]
