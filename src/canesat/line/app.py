"""FastAPI app: POST /callback (LINE webhook, X-Line-Signature verified) and GET /healthz.

Run:  uvicorn --factory canesat.line.app:create_app --host 127.0.0.1 --port 8711
(secrets come from the environment: LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN;
optional DATABASE_URL). See scripts/run_webhook.py for a launcher that loads them from a
JSON secrets file without printing them.
"""

from __future__ import annotations

import logging

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from linebot.v3 import WebhookParser
from linebot.v3.exceptions import InvalidSignatureError

from .client import LineApi, LineClient
from .config import LineSettings
from .handlers import EventRouter
from .store import NullStore, PgStore, Store

log = logging.getLogger("canesat.line")


def create_app(
    settings: LineSettings | None = None,
    line_api: LineApi | None = None,
    store: Store | None = None,
) -> FastAPI:
    settings = settings or LineSettings()
    if not settings.channel_secret:
        raise RuntimeError("LINE_CHANNEL_SECRET is not set")
    if line_api is None:
        if not settings.channel_access_token:
            raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is not set")
        line_api = LineClient(settings.channel_access_token)
    if store is None:
        store = PgStore(settings.database_url) if settings.database_url else NullStore()

    parser = WebhookParser(settings.channel_secret)
    router = EventRouter(line_api, store)

    app = FastAPI(
        title="เบิ่งไฮ่ (BerngHai) LINE webhook",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.router = router

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok", "db": store.status()}

    @app.post("/callback")
    async def callback(
        request: Request,
        background: BackgroundTasks,
        x_line_signature: str | None = Header(default=None),
    ) -> dict[str, str]:
        raw = await request.body()
        if not x_line_signature:
            raise HTTPException(status_code=400, detail="missing X-Line-Signature")
        try:
            body = raw.decode("utf-8")
            events = parser.parse(body, x_line_signature)
        except InvalidSignatureError:
            log.warning("rejected webhook: invalid signature")
            raise HTTPException(status_code=400, detail="invalid signature") from None
        except (UnicodeDecodeError, ValueError, KeyError, TypeError):
            raise HTTPException(status_code=400, detail="bad payload") from None
        log.info("webhook: %d event(s)", len(events))
        # Ack LINE immediately; handle (reply / DB) after the response is sent.
        background.add_task(router.dispatch_all, events)
        return {"status": "ok"}

    return app
