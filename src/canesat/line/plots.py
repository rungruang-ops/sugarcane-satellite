"""Plot registration payload (LIFF page -> POST /api/plots): validation and area in rai."""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from pyproj import Geod
from shapely.geometry import Polygon
from shapely.geometry.polygon import orient
from shapely.validation import explain_validity

M2_PER_RAI = 1600.0
MIN_RAI = 0.5
MAX_RAI = 2000.0
MAX_VERTICES = 300
# Thailand, generous bounding box (lon_min, lat_min, lon_max, lat_max)
THAILAND_BBOX = (97.3, 5.5, 105.7, 20.5)
CONSENT_VERSION = "2026-10-draft1"  # draft text — must be reviewed by a lawyer (design §6.3)

_GEOD = Geod(ellps="WGS84")
_PHONE_RE = re.compile(r"^0\d{8,9}$")


class PlotValidationError(ValueError):
    """Raised with a short Thai message suitable for showing to the user."""


def area_m2(poly: Polygon) -> float:
    """Geodesic area on the WGS84 ellipsoid (lon/lat polygon)."""
    area, _ = _GEOD.geometry_area_perimeter(poly)
    return abs(area)


def area_rai(poly: Polygon) -> float:
    return area_m2(poly) / M2_PER_RAI


def polygon_from_geojson(geom: dict[str, Any]) -> Polygon:
    if not isinstance(geom, dict) or geom.get("type") != "Polygon":
        raise PlotValidationError("ต้องเป็นรูปแปลง (Polygon) 1 รูป")
    rings = geom.get("coordinates")
    if not isinstance(rings, list) or len(rings) != 1 or not isinstance(rings[0], list):
        raise PlotValidationError("รูปแปลงต้องมีขอบเขตเดียว (ไม่มีรูตรงกลาง)")
    try:
        pts = [(float(p[0]), float(p[1])) for p in rings[0]]
    except (TypeError, ValueError, IndexError):
        raise PlotValidationError("พิกัดไม่ถูกต้อง") from None
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    # drop consecutive duplicates (double taps)
    dedup = [p for i, p in enumerate(pts) if i == 0 or p != pts[i - 1]]
    if len(set(dedup)) < 3:
        raise PlotValidationError("ต้องแตะอย่างน้อย 3 จุดรอบแปลง")
    if len(dedup) > MAX_VERTICES:
        raise PlotValidationError(f"จุดมากเกินไป (สูงสุด {MAX_VERTICES} จุด)")
    return Polygon(dedup)


def validate_polygon(geom: dict[str, Any]) -> tuple[Polygon, float]:
    """Return (polygon, area in rai) or raise PlotValidationError."""
    poly = polygon_from_geojson(geom)
    lon0, lat0, lon1, lat1 = THAILAND_BBOX
    minx, miny, maxx, maxy = poly.bounds
    if minx < lon0 or maxx > lon1 or miny < lat0 or maxy > lat1:
        raise PlotValidationError("แปลงต้องอยู่ในประเทศไทย")
    if not poly.is_valid:
        why = explain_validity(poly)
        if "Self-intersection" in why or "intersection" in why.lower():
            raise PlotValidationError("เส้นขอบแปลงตัดกันเอง — ลองแตะจุดตามลำดับรอบแปลง")
        raise PlotValidationError("รูปแปลงไม่ถูกต้อง — ลองวาดใหม่")
    rai = area_rai(poly)
    if rai < MIN_RAI:
        raise PlotValidationError(f"แปลงเล็กเกินไป (≈ {rai:.2f} ไร่ ต้องอย่างน้อย {MIN_RAI} ไร่)")
    if rai > MAX_RAI:
        raise PlotValidationError(f"แปลงใหญ่เกินไป (≈ {rai:,.0f} ไร่ สูงสุด {MAX_RAI:,.0f} ไร่)")
    # store counter-clockwise exterior ring (GeoJSON right-hand rule)
    return orient(poly, sign=1.0), rai


class Consents(BaseModel):
    service: bool = False  # required
    leader_view: bool = False
    research: bool = False


class PlotIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)
    cane_type: Literal["plant", "ratoon", "unknown"] = "unknown"
    # planting date (plant cane) or last harvest (ratoon); "YYYY-MM" or "YYYY-MM-DD"
    ref_date: str | None = None
    geometry: dict[str, Any]
    on_behalf: bool = False
    member_name: str | None = Field(default=None, max_length=80)
    member_phone: str | None = Field(default=None, max_length=20)
    consents: Consents = Field(default_factory=Consents)
    assisted_consent_confirmed: bool = False  # leader read the notice to the owner

    @field_validator("name", "member_name")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        return v.strip() if isinstance(v, str) else v

    def parsed_date(self, today: date | None = None) -> date | None:
        if not self.ref_date:
            return None
        s = self.ref_date.strip()
        try:
            d = date.fromisoformat(s if len(s) > 7 else f"{s}-01")
        except ValueError:
            raise PlotValidationError("วันที่ไม่ถูกต้อง") from None
        today = today or date.today()
        if d > today:
            raise PlotValidationError("วันที่ปลูก/ตัด ต้องไม่อยู่ในอนาคต")
        if d.year < today.year - 6:
            raise PlotValidationError("วันที่ปลูก/ตัด เก่าเกินไป (เกิน 6 ปี)")
        return d

    def normalized_phone(self) -> str | None:
        if not self.member_phone:
            return None
        digits = re.sub(r"[\s\-().]", "", self.member_phone)
        if digits.startswith("+66"):
            digits = "0" + digits[3:]
        if not _PHONE_RE.match(digits):
            raise PlotValidationError("เบอร์โทรไม่ถูกต้อง (เช่น 0812345678)")
        return digits

    def check(self, today: date | None = None) -> dict[str, Any]:
        """Full validation. Returns normalized fields; raises PlotValidationError."""
        if not self.name:
            raise PlotValidationError("กรุณาตั้งชื่อแปลง")
        poly, rai = validate_polygon(self.geometry)
        d = self.parsed_date(today)
        if not self.consents.service:
            raise PlotValidationError("ต้องยินยอมข้อ 'จำเป็น' ก่อนบันทึก")
        phone = None
        if self.on_behalf:
            if not self.member_name:
                raise PlotValidationError("กรุณาใส่ชื่อเจ้าของแปลง (สมาชิก)")
            phone = self.normalized_phone()
            if not self.assisted_consent_confirmed:
                raise PlotValidationError("กรุณายืนยันว่าได้อ่านความยินยอมให้เจ้าของแปลงฟังแล้ว")
        return {
            "polygon": poly,
            "area_rai": round(rai, 2),
            "planting_date": d if self.cane_type == "plant" else None,
            "last_harvest_date": d if self.cane_type == "ratoon" else None,
            "member_phone": phone,
        }
