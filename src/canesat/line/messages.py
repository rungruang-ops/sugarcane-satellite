"""LINE message builders (plain Messaging API JSON dicts) for the เบิ่งไฮ่ bot.

Wording follows docs/design.md §8: short, simple Thai, say *what to do* before *why*, never
say "NDVI", say the satellite does not know the cause, always offer buttons.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from urllib.parse import parse_qs, quote, urlencode

BOT_NAME = "เบิ่งไฮ่"

THAI_MONTHS_SHORT = (
    "ม.ค.",
    "ก.พ.",
    "มี.ค.",
    "เม.ย.",
    "พ.ค.",
    "มิ.ย.",
    "ก.ค.",
    "ส.ค.",
    "ก.ย.",
    "ต.ค.",
    "พ.ย.",
    "ธ.ค.",
)

# Alert feedback buttons (design §5.1 "การเรียนรู้"): code stored in alerts.feedback -> label
FEEDBACK_CHOICES: dict[str, str] = {
    "borer": "🐛 หนอนกอ",
    "white_leaf": "🍃 ใบขาว",
    "weed": "🌿 หญ้า",
    "water_stress": "💧 ขาดน้ำ",
    "no_problem": "✅ ไม่พบปัญหา",
}

# "Did it rain at your farm?" buttons on rain-gap messages (design §8.2)
RAIN_REPORT_CHOICES: dict[str, str] = {
    "none": "ไม่ตก",
    "little": "ตกนิดหน่อย",
    "enough": "ตกพอ",
}

Message = dict[str, Any]


def thai_short_date(d: date) -> str:
    """29 ก.ย. — day + short Thai month (year omitted, as in the design examples)."""
    return f"{d.day} {THAI_MONTHS_SHORT[d.month - 1]}"


# ------------------------------------------------------------------ quick replies
def _qr_message(label: str, text: str) -> dict[str, Any]:
    return {"type": "action", "action": {"type": "message", "label": label, "text": text}}


def _qr_postback(label: str, data: str, display_text: str) -> dict[str, Any]:
    return {
        "type": "action",
        "action": {"type": "postback", "label": label, "data": data, "displayText": display_text},
    }


def main_quick_reply() -> dict[str, Any]:
    return {
        "items": [
            _qr_message("🗺️ แปลงของฉัน", "แปลงของฉัน"),
            _qr_message("📊 เทียบเพื่อนบ้าน", "เทียบเพื่อนบ้าน"),
            _qr_message("🌧️ ฝน", "ฝน"),
            _qr_message("➕ เพิ่มแปลง", "เพิ่มแปลง"),
            _qr_message("❓ เมนู", "เมนู"),
        ]
    }


def text(body: str, *, quick_reply: bool = True) -> Message:
    msg: Message = {"type": "text", "text": body}
    if quick_reply:
        msg["quickReply"] = main_quick_reply()
    return msg


# ------------------------------------------------------------------ chat replies
def welcome(display_name: str | None = None) -> list[Message]:
    hello = f"สวัสดีครับ คุณ{display_name} 🙏" if display_name else "สวัสดีครับ 🙏"
    body = (
        f'{hello}\nขอบคุณที่เพิ่มเพื่อน "{BOT_NAME}" 🌱\n'
        f"{BOT_NAME} ใช้ภาพดาวเทียมช่วยดูไร่อ้อยให้ฟรี:\n"
        "🔴 เตือนเมื่อแปลงของคุณเขียวน้อยกว่าแปลงรอบ ๆ — ชวนไปดูแปลง\n"
        "🟠 เตือนฝนทิ้งช่วง และบอกจังหวะให้น้ำ/ใส่ปุ๋ย\n"
        "📈 เทียบแปลงของคุณกับเพื่อนบ้าน และกับปีที่แล้ว\n\n"
        "⏳ การลงทะเบียนแปลงจะเปิดเร็ว ๆ นี้ (หัวหน้ากลุ่มช่วยลงให้ได้)\n"
        'พิมพ์ "เมนู" เพื่อดูว่าทำอะไรได้บ้าง'
    )
    return [text(body)]


def help_menu() -> list[Message]:
    body = (
        f"📋 {BOT_NAME} ทำอะไรได้บ้าง\n"
        "🗺️ แปลงของฉัน — ดูแปลงที่ลงทะเบียนไว้\n"
        "📊 เทียบเพื่อนบ้าน — อ้อยเราเขียวกว่าหรือน้อยกว่าแปลงรอบ ๆ และปีที่แล้ว\n"
        "🌧️ ฝน — เตือนฝนทิ้งช่วง จังหวะให้น้ำ/ใส่ปุ๋ย\n"
        "➕ เพิ่มแปลง — ลงทะเบียนแปลง (เร็ว ๆ นี้)\n"
        "👥 หัวหน้ากลุ่ม — ช่วยลงทะเบียนให้สมาชิก (เร็ว ๆ นี้)\n\n"
        "กดปุ่มด้านล่าง หรือพิมพ์คำเหล่านี้ได้เลยครับ"
    )
    return [text(body)]


def my_plots(plot_names: list[str] | None) -> list[Message]:
    if plot_names:
        lines = "\n".join(f"• {n}" for n in plot_names[:10])
        more = f"\n…และอีก {len(plot_names) - 10} แปลง" if len(plot_names) > 10 else ""
        body = (
            f"🗺️ แปลงของคุณ ({len(plot_names)} แปลง)\n{lines}{more}\n\n"
            "หน้ากราฟเทียบเพื่อนบ้าน/ปีที่แล้วกำลังจะเปิดเร็ว ๆ นี้ครับ"
        )
    else:
        body = (
            "🗺️ ยังไม่มีแปลงที่ลงทะเบียนไว้ครับ\n"
            "การลงทะเบียนแปลงจะเปิดเร็ว ๆ นี้ — วาดแปลงบนภาพดาวเทียม หรือให้หัวหน้ากลุ่มช่วยลงให้\n"
            f'เมื่อพร้อมแล้ว {BOT_NAME} จะส่งข้อความ "แปลงของคุณพร้อมแล้ว" ให้ครับ'
        )
    return [text(body)]


def compare_neighbours() -> list[Message]:
    body = (
        "📊 เทียบเพื่อนบ้าน & ปีที่แล้ว\n"
        "เมื่อลงทะเบียนแปลงแล้ว จะเห็นกราฟ 3 เส้น: แปลงของคุณปีนี้, แปลงรอบ ๆ ปีนี้ "
        "และแปลงของคุณปีที่แล้ว พร้อมบอกว่า ปกติ / เฝ้าระวัง / ควรไปดู\n"
        "⏳ หน้านี้กำลังจะเปิดเร็ว ๆ นี้ครับ"
    )
    return [text(body)]


def rain_info() -> list[Message]:
    body = (
        "🌧️ ฝน / ความชื้นดิน\n"
        f"เร็ว ๆ นี้ {BOT_NAME} จะเตือนเมื่อฝนไม่ค่อยตกติดต่อกันนาน (เช่น 14 วันขึ้นไป) "
        "และดินแห้งกว่าปกติ พร้อมบอกว่า:\n"
        "✅ ถ้ามีน้ำ: ให้น้ำอ้อยอายุน้อยก่อน ช่วงเช้า/เย็น\n"
        "⏸️ ยังไม่ต้องใส่ปุ๋ยเคมี รอดินชื้นก่อนจะคุ้มกว่า\n"
        "และบอกเมื่อฝนกลับมา = จังหวะดีสำหรับใส่ปุ๋ย\n"
        "(ข้อมูลฝนเป็นภาพรวมของพื้นที่ ไม่ใช่ที่แปลงพอดี)"
    )
    return [text(body)]


def add_plot() -> list[Message]:
    body = (
        "➕ การลงทะเบียนแปลงกำลังจะเปิดเร็ว ๆ นี้ครับ\n"
        "จะวาดแปลงบนภาพดาวเทียม หรือเดินรอบแปลงด้วย GPS ก็ได้ "
        "ถ้าไม่ถนัด หัวหน้ากลุ่มช่วยลงให้ได้ (เจ้าของแปลงต้องกดยินยอมเอง)"
    )
    return [text(body)]


def group_leader() -> list[Message]:
    body = (
        "👥 เมนูหัวหน้ากลุ่ม กำลังจะเปิดเร็ว ๆ นี้ครับ\n"
        "หัวหน้ากลุ่มจะช่วยลงทะเบียนแปลงให้สมาชิก และดูภาพรวมแปลงของกลุ่ม "
        "(เฉพาะสมาชิกที่ยินยอม)"
    )
    return [text(body)]


def fallback() -> list[Message]:
    body = f'{BOT_NAME} ยังตอบได้แค่บางคำครับ 🙏\nลองกดปุ่มด้านล่าง หรือพิมพ์ "เมนู"'
    return [text(body)]


def non_text_received() -> list[Message]:
    body = f'ได้รับแล้วครับ 🙏 ตอนนี้{BOT_NAME}ยังตอบได้เฉพาะข้อความ\nลองพิมพ์ "เมนู" ดูครับ'
    return [text(body)]


def feedback_thanks(answer: str, status: str) -> list[Message]:
    label = FEEDBACK_CHOICES.get(answer, answer)
    if answer == "no_problem":
        body = f"✅ ดีใจด้วยครับ บันทึกว่า {label} แล้ว\nขอบคุณที่ไปดูแปลง ช่วยให้{BOT_NAME}แม่นขึ้นครับ"
    else:
        body = (
            f"🙏 ขอบคุณครับ บันทึกว่า {label} แล้ว\n"
            f"ข้อมูลนี้ช่วยให้{BOT_NAME}แม่นขึ้น ถ้าไม่แน่ใจ ลองปรึกษาเจ้าหน้าที่เกษตรตำบลครับ"
        )
    if status == "not_found":
        body += "\n(ไม่พบการแจ้งเตือนนี้ในระบบแล้ว)"
    return [text(body)]


def rain_report_thanks(answer: str) -> list[Message]:
    label = RAIN_REPORT_CHOICES.get(answer, answer)
    return [text(f'🙏 ขอบคุณครับ บันทึกว่าฝนที่ไร่ "{label}" ช่วยให้ข้อมูลฝนแม่นขึ้นครับ')]


# ------------------------------------------------------------------ postback data
def feedback_postback_data(alert_id: int, answer: str) -> str:
    if answer not in FEEDBACK_CHOICES:
        raise ValueError(f"unknown feedback answer: {answer}")
    return urlencode({"action": "feedback", "alert_id": alert_id, "answer": answer})


def parse_postback_data(data: str) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(data or "", keep_blank_values=True).items() if v}


# ------------------------------------------------------------------ Flex: anomaly alert
def _feedback_button(alert_id: int, answer: str) -> dict[str, Any]:
    label = FEEDBACK_CHOICES[answer]
    return {
        "type": "button",
        "style": "secondary",
        "height": "sm",
        "flex": 1,
        "action": {
            "type": "postback",
            "label": label,
            "data": feedback_postback_data(alert_id, answer),
            "displayText": label.split(" ", 1)[-1],
        },
    }


def maps_directions_url(lat: float, lon: float) -> str:
    return (
        "https://www.google.com/maps/dir/?api=1&destination="
        + quote(f"{lat:.6f},{lon:.6f}", safe="")
        + "&travelmode=driving"
    )


def build_greenness_alert_flex(
    *,
    alert_id: int,
    plot_name: str,
    obs_date: date,
    area_hint: str | None = None,
    image_url: str | None = None,
    graph_url: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    visit_within: str = "3–5 วัน",
) -> Message:
    """Flex message for a 🔴 "less green than neighbours" alert (design §8.1, mockup 01).

    Not sent by the webhook (alerts are push messages, owned by the future notify job, which
    must respect the 300/month quota). ``image_url`` must be a public HTTPS PNG/JPEG of the plot
    with the anomalous part shaded red.
    """
    title = f'🔴 แปลง "{plot_name}" ควรไปดู'
    where = f" โดยเฉพาะ{area_hint}" if area_hint else ""
    detail = f"ภาพดาวเทียม {thai_short_date(obs_date)} อ้อยแปลงนี้เขียวน้อยกว่าแปลงรอบ ๆ{where}" + (
        " (สีแดงในรูป)" if image_url else ""
    )
    cause = f"อาจเป็นหนอนกอ ใบขาว หญ้า หรือขาดน้ำ — ลองไปดูภายใน {visit_within}"

    link_buttons: list[dict[str, Any]] = []
    if lat is not None and lon is not None:
        link_buttons.append(
            {
                "type": "button",
                "style": "primary",
                "color": "#06C755",
                "height": "sm",
                "flex": 1,
                "action": {
                    "type": "uri",
                    "label": "📍 นำทางไปแปลง",
                    "uri": maps_directions_url(lat, lon),
                },
            }
        )
    if graph_url:
        link_buttons.append(
            {
                "type": "button",
                "style": "link",
                "height": "sm",
                "flex": 1,
                "action": {"type": "uri", "label": "📈 ดูกราฟ", "uri": graph_url},
            }
        )

    answers = list(FEEDBACK_CHOICES)
    footer_contents: list[dict[str, Any]] = []
    if link_buttons:
        footer_contents.append(
            {"type": "box", "layout": "horizontal", "spacing": "sm", "contents": link_buttons}
        )
        footer_contents.append({"type": "separator", "margin": "md"})
    footer_contents += [
        {
            "type": "text",
            "text": "ไปดูแล้วเจออะไร? กดตอบได้เลย ช่วยให้ระบบแม่นขึ้น",
            "size": "sm",
            "weight": "bold",
            "wrap": True,
            "margin": "md",
        },
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [_feedback_button(alert_id, a) for a in answers[:3]],
        },
        {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [_feedback_button(alert_id, a) for a in answers[3:]],
        },
    ]

    bubble: dict[str, Any] = {
        "type": "bubble",
        "size": "kilo",
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "text",
                    "text": title,
                    "weight": "bold",
                    "size": "md",
                    "color": "#C62828",
                    "wrap": True,
                },
                {"type": "text", "text": detail, "size": "sm", "wrap": True},
                {"type": "text", "text": cause, "size": "sm", "wrap": True},
                {
                    "type": "text",
                    "text": "ดาวเทียมบอกได้แค่ว่าเขียวน้อยกว่า แต่ไม่รู้สาเหตุ",
                    "size": "xs",
                    "color": "#888888",
                    "wrap": True,
                },
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents,
        },
    }
    if image_url:
        bubble["hero"] = {
            "type": "image",
            "url": image_url,
            "size": "full",
            "aspectRatio": "20:7",
            "aspectMode": "cover",
        }

    return {
        "type": "flex",
        "altText": f"{title} — เขียวน้อยกว่าแปลงรอบ ๆ ({thai_short_date(obs_date)})",
        "contents": bubble,
        "quickReply": {
            "items": [
                _qr_postback(
                    FEEDBACK_CHOICES[a],
                    feedback_postback_data(alert_id, a),
                    FEEDBACK_CHOICES[a].split(" ", 1)[-1],
                )
                for a in answers
            ]
        },
    }
