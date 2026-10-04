"""LIFF plot registration: GET /liff/register (page), POST/GET /api/plots (ID-token auth)."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

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
        "center": [102.55, 16.45],  # Khon Kaen pilot (between Mueang and Nong Ruea)
        "zoom": 11,
    }


def render_register_page(settings: LineSettings) -> str:
    html = (STATIC_DIR / "register.html").read_text(encoding="utf-8")
    cfg = json.dumps(page_config(settings), ensure_ascii=False)
    # safe inside <script>: no "</script>", "<!--" or HTML-significant chars
    cfg = cfg.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return html.replace(_PLACEHOLDER, cfg)


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

    @r.get("/liff/register", response_class=HTMLResponse)
    def register_page() -> HTMLResponse:
        return HTMLResponse(render_register_page(settings), headers={"Cache-Control": "no-store"})

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
            "plot registered id=%s area_rai=%.1f on_behalf=%s",
            out["id"],
            out["area_rai"],
            body.on_behalf,
        )
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

    return r
