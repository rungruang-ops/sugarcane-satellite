"""Rich Menu spec (design mockup 01: 6 buttons, 3 x 2). NOT created automatically.

Until LIFF exists every button sends a keyword the webhook understands; later the first three
become LIFF ``uri`` actions. Create it with scripts/create_rich_menu.py (manual step).
"""

from __future__ import annotations

from typing import Any

WIDTH, HEIGHT = 2500, 1686
COLS, ROWS = 3, 2

# (label shown on the image, text sent when tapped)
BUTTONS: list[tuple[str, str]] = [
    ("➕ เพิ่มแปลง", "เพิ่มแปลง"),
    ("🗺️ แปลงของฉัน", "แปลงของฉัน"),
    ("📊 เทียบเพื่อนบ้าน", "เทียบเพื่อนบ้าน"),
    ("🌧️ ฝน/ความชื้นดิน", "ฝน"),
    ("👥 หัวหน้ากลุ่ม", "หัวหน้ากลุ่ม"),
    ("❓ วิธีใช้/ติดต่อ", "เมนู"),
]


def rich_menu_spec(name: str = "bernghai-main-v1") -> dict[str, Any]:
    col_w = [WIDTH // COLS] * COLS
    col_w[-1] += WIDTH - sum(col_w)
    row_h = [HEIGHT // ROWS] * ROWS
    row_h[-1] += HEIGHT - sum(row_h)
    areas = []
    for i, (_label, text) in enumerate(BUTTONS):
        r, c = divmod(i, COLS)
        areas.append(
            {
                "bounds": {
                    "x": sum(col_w[:c]),
                    "y": sum(row_h[:r]),
                    "width": col_w[c],
                    "height": row_h[r],
                },
                "action": {"type": "message", "label": text[:20], "text": text},
            }
        )
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": name,
        "chatBarText": "เมนู",
        "areas": areas,
    }
