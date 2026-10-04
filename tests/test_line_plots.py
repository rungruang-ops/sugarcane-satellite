"""LIFF plot payload: polygon validation, area in rai, form checks, ID token verification."""

import math
from datetime import date

import pytest

from canesat.line.idtoken import InvalidIdToken, LineIdTokenVerifier
from canesat.line.plots import PlotIn, PlotValidationError, area_rai, validate_polygon

LON, LAT = 102.55, 16.45


def square(side_m, lon=LON, lat=LAT, close=True):
    dlat = side_m / 111_320.0
    dlon = side_m / (111_320.0 * math.cos(math.radians(lat)))
    ring = [[lon, lat], [lon + dlon, lat], [lon + dlon, lat + dlat], [lon, lat + dlat]]
    if close:
        ring.append(ring[0])
    return {"type": "Polygon", "coordinates": [ring]}


# ------------------------------------------------------------------ area
@pytest.mark.parametrize(("side", "rai"), [(40, 1.0), (100, 6.25), (400, 100.0)])
def test_area_rai_of_squares(side, rai):
    poly, got = validate_polygon(square(side))
    assert got == pytest.approx(rai, rel=0.01)  # 1 rai = 1600 m²
    assert area_rai(poly) == pytest.approx(got)


def test_open_ring_and_duplicate_taps_accepted():
    g = square(100, close=False)
    ring = g["coordinates"][0]
    g["coordinates"][0] = [ring[0], ring[1], ring[1], ring[2], ring[3]]  # double tap
    poly, rai = validate_polygon(g)
    assert rai == pytest.approx(6.25, rel=0.01)
    assert len(poly.exterior.coords) == 5
    assert poly.exterior.is_ccw  # stored with GeoJSON right-hand rule


# ------------------------------------------------------------------ rejects
def test_self_intersecting_bowtie_rejected():
    ring = square(100)["coordinates"][0]
    bowtie = [ring[0], ring[2], ring[1], ring[3], ring[0]]
    with pytest.raises(PlotValidationError, match="ตัดกันเอง"):
        validate_polygon({"type": "Polygon", "coordinates": [bowtie]})


@pytest.mark.parametrize(
    ("geom", "msg"),
    [
        (square(20), "เล็กเกินไป"),  # 0.25 rai
        (square(2000), "ใหญ่เกินไป"),  # 2,500 rai
        (square(100, lon=139.7, lat=35.6), "ประเทศไทย"),  # Tokyo
        ({"type": "Point", "coordinates": [LON, LAT]}, "Polygon"),
        ({"type": "Polygon", "coordinates": [[[LON, LAT], [LON + 0.001, LAT]]]}, "3 จุด"),
        ({"type": "Polygon", "coordinates": [[["x", LAT]] * 4]}, "พิกัด"),
        (
            {"type": "Polygon", "coordinates": square(100)["coordinates"] * 2},
            "ขอบเขตเดียว",
        ),
    ],
)
def test_invalid_geometries(geom, msg):
    with pytest.raises(PlotValidationError, match=msg):
        validate_polygon(geom)


# ------------------------------------------------------------------ form
def payload(**kw):
    base = {
        "name": " ไร่หลังบ้าน ",
        "cane_type": "ratoon",
        "ref_date": "2026-02",
        "geometry": square(200),
        "consents": {"service": True, "leader_view": True},
    }
    base.update(kw)
    return PlotIn(**base)


def test_check_ok_ratoon_month():
    p = payload()
    out = p.check(today=date(2026, 10, 5))
    assert p.name == "ไร่หลังบ้าน"
    assert out["last_harvest_date"] == date(2026, 2, 1) and out["planting_date"] is None
    assert out["area_rai"] == pytest.approx(25.0, rel=0.01)


def test_check_plant_full_date():
    out = payload(cane_type="plant", ref_date="2025-11-20").check(today=date(2026, 10, 5))
    assert out["planting_date"] == date(2025, 11, 20) and out["last_harvest_date"] is None


@pytest.mark.parametrize(
    ("kw", "msg"),
    [
        ({"consents": {"service": False}}, "จำเป็น"),
        ({"ref_date": "2027-01"}, "อนาคต"),
        ({"ref_date": "2010-01"}, "เก่าเกินไป"),
        ({"ref_date": "ก.พ."}, "วันที่ไม่ถูกต้อง"),
        ({"on_behalf": True}, "ชื่อเจ้าของแปลง"),
        ({"on_behalf": True, "member_name": "นางบุญมี"}, "อ่านความยินยอม"),
        (
            {
                "on_behalf": True,
                "member_name": "นางบุญมี",
                "member_phone": "12345",
                "assisted_consent_confirmed": True,
            },
            "เบอร์โทร",
        ),
    ],
)
def test_check_rejects(kw, msg):
    with pytest.raises(PlotValidationError, match=msg):
        payload(**kw).check(today=date(2026, 10, 5))


@pytest.mark.parametrize("phone", ["081-234-5678", "+66812345678", "0812345678", "02 123 4567"])
def test_on_behalf_phone_normalized(phone):
    out = payload(
        on_behalf=True,
        member_name="นางบุญมี",
        member_phone=phone,
        assisted_consent_confirmed=True,
    ).check(today=date(2026, 10, 5))
    assert out["member_phone"].startswith("0") and out["member_phone"].isdigit()


# ------------------------------------------------------------------ ID token
class FakePost:
    def __init__(self, status=200, body=None):
        self.calls = []
        self.status = status
        self.body = body or {
            "iss": "https://access.line.me",
            "sub": "U" + "b" * 32,
            "aud": "2011859249",
            "exp": 4_000_000_000,
            "name": "สมาน",
        }

    def __call__(self, url, data, timeout):
        self.calls.append((url, data))
        return self.status, self.body


def test_id_token_verified_and_cached():
    post = FakePost()
    v = LineIdTokenVerifier("2011859249", post=post)
    ident = v.verify("tok")
    assert ident.user_id == "U" + "b" * 32 and ident.name == "สมาน"
    assert v.verify("tok") == ident
    assert len(post.calls) == 1  # cached
    url, data = post.calls[0]
    assert url == "https://api.line.me/oauth2/v2.1/verify"
    assert data == {"id_token": "tok", "client_id": "2011859249"}


def test_id_token_rejected_by_line():
    v = LineIdTokenVerifier(
        "2011859249", post=FakePost(400, {"error": "invalid_request", "error_description": "x"})
    )
    with pytest.raises(InvalidIdToken):
        v.verify("tok")


@pytest.mark.parametrize("patch", [{"aud": "999"}, {"iss": "https://evil.example"}, {"sub": None}])
def test_id_token_wrong_claims(patch):
    post = FakePost()
    post.body = {**post.body, **patch}
    with pytest.raises(InvalidIdToken):
        LineIdTokenVerifier("2011859249", post=post).verify("tok")


def test_id_token_empty():
    with pytest.raises(InvalidIdToken):
        LineIdTokenVerifier("1", post=FakePost()).verify("")
