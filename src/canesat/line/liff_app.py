"""LIFF pages + authenticated APIs: register, chart, consent confirm."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from .config import LineSettings
from .idtoken import IdTokenVerifier, InvalidIdToken, LineIdentity
from .ingest_queue import IngestQueue
from .plots import CONSENT_VERSION, MAX_RAI, MIN_RAI, THAILAND_BBOX, PlotIn, PlotValidationError
from .store import PlotLimitReached, Store, StoreUnavailable

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
_PLACEHOLDER = "/*__CANESAT_CONFIG__*/{}"


def page_config(settings: LineSettings) -> dict[str, Any]:
    return {
        "liffId": settings.liff_id or "",
        "consentVersion": CONSENT_VERSION,
        "minRai": MIN_RAI,
        "maxRai": MAX_RAI,
        "bbox": THAILAND_BBOX,
        "center": [102.55, 16.45],
        "zoom": 11,
    }


def render_page(name: str, settings: LineSettings) -> str:
    html = (STATIC_DIR / name).read_text(encoding="utf-8")
    cfg = json.dumps(page_config(settings), ensure_ascii=False)
    cfg = cfg.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return html.replace(_PLACEHOLDER, cfg)


def render_register_page(settings: LineSettings) -> str:
    return render_page("register.html", settings)


def _prior_year_aligned(series: list[dict[str, Any]]) -> list[float | None]:
    """Align prior-calendar-year NDVI onto this year's dates (match month-day)."""
    by_md = {}
    for row in series:
        y, md = row["date"][:4], row["date"][5:]
        by_md.setdefault(md, {})[y] = row["ndvi"]
    years = sorted({r["date"][:4] for r in series})
    if len(years) < 2:
        return [None] * len(series)
    this_year = years[-1]
    prior_year = str(int(this_year) - 1)
    out = []
    for row in series:
        if row["date"][:4] != this_year:
            out.append(None)
            continue
        out.append(by_md.get(row["date"][5:], {}).get(prior_year))
    return out


class ConfirmIn(BaseModel):
    token: str = Field(min_length=8, max_length=80)


def build_liff_router(
    settings: LineSettings,
    store: Store,
    verifier: IdTokenVerifier,
    ingest: IngestQueue | None,
) -> APIRouter:
    r = APIRouter()

    def identity(authorization: str | None) -> LineIdentity:
        if not authorization or not authorization.lower().startswith("bearer "):
            raise HTTPException(401, detail="กรุณาเปิดจาก LINE (ไม่พบการยืนยันตัวตน)")
        try:
            return verifier.verify(authorization[7:].strip())
        except InvalidIdToken:
            raise HTTPException(401, detail="การยืนยันตัวตนหมดอายุ — ปิดแล้วเปิดใหม่อีกครั้ง") from None

    def optional_identity(authorization: str | None) -> LineIdentity | None:
        if not authorization:
            return None
        try:
            return identity(authorization)
        except HTTPException:
            return None

    @r.get("/liff/register", response_class=HTMLResponse)
    def register_page() -> HTMLResponse:
        return HTMLResponse(
            render_page("register.html", settings), headers={"Cache-Control": "no-store"}
        )

    @r.get("/liff/chart", response_class=HTMLResponse)
    def chart_page() -> HTMLResponse:
        return HTMLResponse(
            render_page("chart.html", settings), headers={"Cache-Control": "no-store"}
        )

    @r.get("/liff/consent", response_class=HTMLResponse)
    def consent_page() -> HTMLResponse:
        return HTMLResponse(
            render_page("consent.html", settings), headers={"Cache-Control": "no-store"}
        )

    @r.post("/api/plots", status_code=201)
    async def create_plot(
        body: PlotIn, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        who = await run_in_threadpool(identity, authorization)
        try:
            checked = body.check()
        except PlotValidationError as exc:
            raise HTTPException(422, detail=str(exc)) from None
        reg = {
            "line_user_id": who.user_id,
            "display_name": who.name,
            "name": body.name,
            "polygon": checked["polygon"],
            "area_rai": checked["area_rai"],
            "cane_type": body.cane_type,
            "planting_date": checked["planting_date"],
            "last_harvest_date": checked["last_harvest_date"],
            "on_behalf": body.on_behalf,
            "member_name": body.member_name if body.on_behalf else None,
            "member_phone": checked["member_phone"],
            "consents": body.consents.model_dump(),
            "consent_version": CONSENT_VERSION,
        }
        try:
            out = await run_in_threadpool(store.create_plot, reg)
        except StoreUnavailable:
            raise HTTPException(503, detail="ระบบบันทึกขัดข้องชั่วคราว ลองใหม่อีกครั้ง") from None
        except PlotLimitReached:
            raise HTTPException(409, detail="ลงทะเบียนได้สูงสุด 50 แปลงต่อคน") from None
        log.info(
            "plot registered id=%s area_rai=%.1f on_behalf=%s consent_pending=%s",
            out["id"],
            out["area_rai"],
            body.on_behalf,
            out.get("consent_pending"),
        )
        if out.get("consent_token") and settings.liff_id:
            out["consent_url"] = (
                f"https://liff.line.me/{settings.liff_id}/consent?token={out['consent_token']}"
            )
            # Prefer our hosted path if LIFF endpoint is the register page only —
            # also expose the direct web path for the consent page.
            out["consent_path"] = f"/liff/consent?token={out['consent_token']}"
        if ingest is not None:
            ingest.enqueue(out["id"])
            out["ingest"] = "queued"
        else:
            out["ingest"] = "disabled"
        return out

    @r.get("/api/plots")
    async def my_plots(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        who = await run_in_threadpool(identity, authorization)
        try:
            plots = await run_in_threadpool(store.list_plots, who.user_id)
        except StoreUnavailable:
            raise HTTPException(503, detail="ระบบขัดข้องชั่วคราว ลองใหม่อีกครั้ง") from None
        return {"plots": plots}

    @r.get("/api/plots/{plot_id}/chart")
    async def plot_chart(
        plot_id: int, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        who = await run_in_threadpool(optional_identity, authorization)
        uid = who.user_id if who else None
        data = await run_in_threadpool(store.chart_series, plot_id, uid)
        if data is None:
            # distinguish missing vs forbidden: try without auth filter when no uid
            if uid is None:
                raise HTTPException(401, detail="กรุณาเปิดจาก LINE เพื่อยืนยันตัวตน")
            raise HTTPException(404, detail="ไม่พบแปลง หรือไม่มีสิทธิ์ดู")
        # Restrict unauthenticated access: chart_series with uid=None returns data for any plot.
        # Require auth for privacy.
        if uid is None:
            raise HTTPException(401, detail="กรุณาเปิดจาก LINE เพื่อยืนยันตัวตน")
        series = data["series"]
        this_year = max((s["date"][:4] for s in series), default=None)
        current = [s for s in series if this_year and s["date"].startswith(this_year)]
        data["series"] = current or series
        data["prior_year_aligned"] = _prior_year_aligned(series)
        data["prior_year"] = [
            s for s in series if this_year and s["date"].startswith(str(int(this_year) - 1))
        ]
        return data

    @r.post("/api/consents/confirm")
    async def confirm_consent(
        body: ConfirmIn, authorization: str | None = Header(default=None)
    ) -> dict[str, Any]:
        who = await run_in_threadpool(identity, authorization)
        status = await run_in_threadpool(
            store.confirm_consent, body.token, who.user_id, datetime.now(tz=UTC)
        )
        if status == "not_found":
            raise HTTPException(404, detail="ไม่พบลิงก์ยืนยันนี้ หรือใช้ไปแล้ว")
        if status == "forbidden":
            raise HTTPException(403, detail="ต้องยืนยันด้วยบัญชี LINE ของเจ้าของแปลง")
        if status == "unavailable":
            raise HTTPException(503, detail="ระบบขัดข้องชั่วคราว ลองใหม่อีกครั้ง")
        return {"status": "ok"}

    return r
