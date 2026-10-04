"""Flex message builder for the 🔴 greenness alert (design §8.1 / mockup 01)."""

import json
from datetime import date

from linebot.v3.messaging import FlexMessage, ReplyMessageRequest

from canesat.line.messages import (
    FEEDBACK_CHOICES,
    build_greenness_alert_flex,
    parse_postback_data,
    thai_short_date,
)
from canesat.line.richmenu import HEIGHT, WIDTH, rich_menu_spec


def _walk(node, kind):
    if isinstance(node, dict):
        if node.get("type") == kind:
            yield node
        for v in node.values():
            yield from _walk(v, kind)
    elif isinstance(node, list):
        for v in node:
            yield from _walk(v, kind)


def full_alert():
    return build_greenness_alert_flex(
        alert_id=123,
        plot_name="ไร่หลังบ้าน",
        obs_date=date(2026, 9, 29),
        area_hint="ฝั่งเหนือ",
        image_url="https://example.com/plot123.png",
        graph_url="https://example.com/plot/123",
        lat=16.5589,
        lon=102.4372,
    )


def test_thai_short_date():
    assert thai_short_date(date(2026, 9, 29)) == "29 ก.ย."
    assert thai_short_date(date(2026, 1, 1)) == "1 ม.ค."


def test_alert_flex_json_structure():
    msg = full_alert()
    assert msg["type"] == "flex"
    assert msg["altText"].startswith('🔴 แปลง "ไร่หลังบ้าน" ควรไปดู')
    assert len(msg["altText"]) <= 400
    bubble = msg["contents"]
    assert bubble["type"] == "bubble"
    assert bubble["hero"]["url"] == "https://example.com/plot123.png"

    texts = [t["text"] for t in _walk(bubble["body"], "text")]
    assert texts[0] == '🔴 แปลง "ไร่หลังบ้าน" ควรไปดู'
    assert texts[1] == ("ภาพดาวเทียม 29 ก.ย. อ้อยแปลงนี้เขียวน้อยกว่าแปลงรอบ ๆ โดยเฉพาะฝั่งเหนือ (สีแดงในรูป)")
    assert "หนอนกอ ใบขาว หญ้า หรือขาดน้ำ" in texts[2] and "3–5 วัน" in texts[2]
    assert all("NDVI" not in t for t in texts)

    buttons = list(_walk(bubble["footer"], "button"))
    uris = [b["action"] for b in buttons if b["action"]["type"] == "uri"]
    assert [u["label"] for u in uris] == ["📍 นำทางไปแปลง", "📈 ดูกราฟ"]
    assert "destination=16.558900%2C102.437200" in uris[0]["uri"]
    assert uris[1]["uri"] == "https://example.com/plot/123"

    postbacks = [b["action"] for b in buttons if b["action"]["type"] == "postback"]
    assert [parse_postback_data(p["data"])["answer"] for p in postbacks] == list(FEEDBACK_CHOICES)
    assert {p["displayText"] for p in postbacks} == {
        "หนอนกอ",
        "ใบขาว",
        "หญ้า",
        "ขาดน้ำ",
        "ไม่พบปัญหา",
    }
    for p in postbacks:
        assert parse_postback_data(p["data"]) == {
            "action": "feedback",
            "alert_id": "123",
            "answer": parse_postback_data(p["data"])["answer"],
        }
        assert len(p["data"]) <= 300 and len(p["label"]) <= 40

    qr = msg["quickReply"]["items"]
    assert len(qr) == 5 and all(len(i["action"]["label"]) <= 20 for i in qr)


def test_alert_flex_json_snapshot_is_stable_and_serialisable():
    a, b = full_alert(), full_alert()
    assert json.dumps(a, ensure_ascii=False, sort_keys=True) == json.dumps(
        b, ensure_ascii=False, sort_keys=True
    )


def test_alert_flex_validates_with_line_sdk_models():
    msg = full_alert()
    FlexMessage.from_dict(msg)  # raises on schema errors
    ReplyMessageRequest.from_dict({"replyToken": "t", "messages": [msg]})


def test_alert_flex_minimal_has_no_hero_or_links():
    msg = build_greenness_alert_flex(alert_id=7, plot_name="ไร่โนนสูง", obs_date=date(2026, 10, 3))
    bubble = msg["contents"]
    assert "hero" not in bubble
    assert not [b for b in _walk(bubble["footer"], "button") if b["action"]["type"] == "uri"]
    assert "(สีแดงในรูป)" not in json.dumps(bubble, ensure_ascii=False)
    FlexMessage.from_dict(msg)


def test_rich_menu_spec_has_six_tiles_covering_canvas():
    spec = rich_menu_spec()
    areas = spec["areas"]
    assert len(areas) == 6
    assert sum(a["bounds"]["width"] * a["bounds"]["height"] for a in areas) == WIDTH * HEIGHT
    assert [a["action"]["text"] for a in areas] == [
        "เพิ่มแปลง",
        "แปลงของฉัน",
        "เทียบเพื่อนบ้าน",
        "ฝน",
        "หัวหน้ากลุ่ม",
        "เมนู",
    ]
