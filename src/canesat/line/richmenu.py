"""Rich Menu for เบิ่งไฮ่ (design mockup 01: 6 tiles, 3 x 2) — spec + image renderer.

Created/updated by scripts/create_rich_menu.py (manual step). Tile "เพิ่มแปลง" opens the LIFF
registration page when LIFF_ID is known; every other tile sends a keyword the webhook answers.
"""

from __future__ import annotations

import glob
import os
from pathlib import Path
from typing import Any

WIDTH, HEIGHT = 2500, 1686
COLS, ROWS = 3, 2
NAME_PREFIX = "bernghai-main"  # menus with this name prefix are owned (and replaced) by the script

# (key, label on the image, subtitle, keyword text sent when tapped)
BUTTONS: list[tuple[str, str, str, str]] = [
    ("add", "เพิ่มแปลง", "วาดขอบเขตแปลง", "เพิ่มแปลง"),
    ("plots", "แปลงของฉัน", "แปลงที่ลงทะเบียน", "แปลงของฉัน"),
    ("compare", "เทียบเพื่อนบ้าน", "เทียบแปลงรอบ ๆ / ปีก่อน", "เทียบเพื่อนบ้าน"),
    ("rain", "ฝน/ความชื้นดิน", "เตือนฝนทิ้งช่วง", "ฝน"),
    ("leader", "หัวหน้ากลุ่ม", "ลงทะเบียนแทนสมาชิก", "หัวหน้ากลุ่ม"),
    ("help", "วิธีใช้/ติดต่อ", "ถาม-ตอบ ถอนความยินยอม", "เมนู"),
]


def _grid() -> tuple[list[int], list[int]]:
    col_w = [WIDTH // COLS] * COLS
    col_w[-1] += WIDTH - sum(col_w)
    row_h = [HEIGHT // ROWS] * ROWS
    row_h[-1] += HEIGHT - sum(row_h)
    return col_w, row_h


def tile_bounds() -> list[tuple[int, int, int, int]]:
    col_w, row_h = _grid()
    out = []
    for i in range(len(BUTTONS)):
        r, c = divmod(i, COLS)
        out.append((sum(col_w[:c]), sum(row_h[:r]), col_w[c], row_h[r]))
    return out


def rich_menu_spec(liff_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    areas = []
    for (key, label, _sub, text), (x, y, w, h) in zip(BUTTONS, tile_bounds(), strict=True):
        if key == "add" and liff_id:
            action = {"type": "uri", "label": label, "uri": f"https://liff.line.me/{liff_id}"}
        else:
            action = {"type": "message", "label": label[:20], "text": text}
        areas.append({"bounds": {"x": x, "y": y, "width": w, "height": h}, "action": action})
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": name or f"{NAME_PREFIX}-{'liff' if liff_id else 'msg'}",
        "chatBarText": "เมนู",
        "areas": areas,
    }


# ------------------------------------------------------------------ image
GREEN = (46, 125, 50)
GREEN_DARK = (27, 94, 32)
GREEN_LINE = (6, 199, 85)
TILE_BG = (246, 251, 246)
ICON_BG = (227, 243, 228)
BORDER = (214, 230, 216)
GREY = (96, 110, 100)

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/sand-box/google/Sarabun/Sarabun-{w}.ttf",
    "/usr/share/fonts/truetype/sarabun/Sarabun-{w}.ttf",
    "/usr/share/fonts/**/Sarabun-{w}.ttf",
    "/usr/share/fonts/**/NotoSansThai-{w}.ttf",
]


def find_thai_font(weight: str = "Bold") -> str | None:
    env = os.environ.get("CANESAT_THAI_FONT")
    if env and Path(env).exists():
        return env
    for pat in _FONT_CANDIDATES:
        hits = glob.glob(pat.format(w=weight), recursive=True)
        if hits:
            return sorted(hits)[0]
    return None


def _icon(d, key: str, cx: int, cy: int, s: int) -> None:
    """Simple vector icons (no emoji font needed). s = icon radius."""
    w = max(10, s // 9)
    d.ellipse((cx - s, cy - s, cx + s, cy + s), fill=ICON_BG)
    k = s * 0.55
    if key == "add":
        d.rounded_rectangle((cx - k, cy - w, cx + k, cy + w), radius=w, fill=GREEN)
        d.rounded_rectangle((cx - w, cy - k, cx + w, cy + k), radius=w, fill=GREEN)
    elif key == "plots":
        poly = [
            (cx - k, cy - k * 0.2),
            (cx + k * 0.3, cy - k * 0.75),
            (cx + k, cy + k * 0.15),
            (cx - k * 0.1, cy + k * 0.8),
        ]
        d.polygon(poly, fill=(200, 236, 205), outline=GREEN)
        d.line([*poly, poly[0]], fill=GREEN_LINE, width=w)
        for p in poly:
            d.ellipse(
                (p[0] - w, p[1] - w, p[0] + w, p[1] + w), fill="white", outline=GREEN, width=w // 2
            )
    elif key == "compare":
        bw = k * 0.42
        for i, hgt in enumerate((0.7, 1.25, 0.95)):
            x0 = cx - k + i * (bw + k * 0.37)
            d.rounded_rectangle(
                (x0, cy + k - hgt * k * 1.2, x0 + bw, cy + k),
                radius=w // 2,
                fill=GREEN if i != 1 else GREEN_LINE,
            )
        d.line((cx - k * 1.1, cy + k + w, cx + k * 1.1, cy + k + w), fill=GREEN_DARK, width=w // 2)
    elif key == "rain":
        cy0 = cy - k * 0.25
        d.ellipse((cx - k, cy0 - k * 0.35, cx - k * 0.1, cy0 + k * 0.45), fill=GREEN)
        d.ellipse((cx - k * 0.55, cy0 - k * 0.75, cx + k * 0.45, cy0 + k * 0.35), fill=GREEN)
        d.ellipse((cx, cy0 - k * 0.45, cx + k, cy0 + k * 0.45), fill=GREEN)
        d.rectangle((cx - k * 0.6, cy0, cx + k * 0.6, cy0 + k * 0.45), fill=GREEN)
        for dx in (-0.5, 0, 0.5):
            x = cx + dx * k
            d.line((x, cy0 + k * 0.7, x - k * 0.15, cy0 + k * 1.15), fill=(30, 136, 229), width=w)
    elif key == "leader":
        k = s * 0.75
        for dx, sc in ((-0.55, 0.75), (0.55, 0.75), (0, 1.0)):
            hx, hr = cx + dx * k, k * 0.32 * sc
            hy = cy - k * 0.35 + (1 - sc) * k * 0.3
            col = GREEN if sc == 1.0 else (120, 180, 124)
            d.pieslice(
                (hx - hr * 1.7, hy + hr * 0.9, hx + hr * 1.7, hy + hr * 4.3), 180, 360, fill=col
            )
            d.ellipse((hx - hr, hy - hr, hx + hr, hy + hr), fill=col, outline=ICON_BG, width=w // 2)
    elif key == "help":
        font = _font(int(s * 1.25), "Bold")
        d.text((cx, cy + s * 0.04), "?", font=font, fill=GREEN, anchor="mm")


def _font(size: int, weight: str = "Bold"):
    from PIL import ImageFont

    path = find_thai_font(weight)
    if path:
        return ImageFont.truetype(path, size)
    return ImageFont.load_default(size)


def _fit(d, text: str, size: int, max_w: int, weight: str):
    while True:
        f = _font(size, weight)
        if size <= 40 or d.textlength(text, font=f) <= max_w:
            return f
        size -= 4


def render_rich_menu_image(out_path: str | Path) -> Path:
    """Draw the 2500x1686 PNG (green theme as in mockup 01) and save it."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (WIDTH, HEIGHT), TILE_BG)
    d = ImageDraw.Draw(img)
    for (key, label, subtitle, _t), (x, y, w, h) in zip(BUTTONS, tile_bounds(), strict=True):
        d.rectangle((x, y, x + w - 1, y + h - 1), outline=BORDER, width=6)
        cx = x + w // 2
        _icon(d, key, cx, y + int(h * 0.36), int(min(w, h) * 0.2))
        title = _fit(d, label, 112, int(w * 0.86), "Bold")
        sub = _fit(d, subtitle, 62, int(w * 0.86), "Medium")
        d.text((cx, y + int(h * 0.70)), label, font=title, fill=GREEN_DARK, anchor="mm")
        d.text((cx, y + int(h * 0.86)), subtitle, font=sub, fill=GREY, anchor="mm")
    # thin brand strip on top
    d.rectangle((0, 0, WIDTH, 10), fill=GREEN_LINE)
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out, format="PNG", optimize=True)
    return out
