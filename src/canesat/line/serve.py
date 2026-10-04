"""Production entry point for the เบิ่งไฮ่ webhook (container / Railway).

    python -m canesat.line.serve

* Reads everything from the process environment (LINE_CHANNEL_SECRET, LINE_CHANNEL_ACCESS_TOKEN,
  DATABASE_URL, optional LIFF_ID / CANESAT_PHONE_KEY). Secrets are never printed.
* If DATABASE_URL is set, applies pending SQL migrations first (PostGIS extension + schema),
  retrying while the database is still starting. A database that stays unreachable is logged
  and the server starts anyway (the bot degrades to "no DB", like in local dev).
* Binds 0.0.0.0:$PORT (default 8080) and trusts X-Forwarded-* from the platform proxy.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
import time
from collections.abc import Callable

log = logging.getLogger("canesat.serve")


def migrate_with_retry(
    dsn: str,
    *,
    attempts: int = 30,
    delay_s: float = 2.0,
    migrate: Callable[[str], list[str]] | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[str] | None:
    """Apply migrations, retrying on connection errors. Returns names applied, None on failure."""
    import psycopg

    if migrate is None:

        def migrate(url: str) -> list[str]:
            from .. import db

            with db.connect(url, connect_timeout=5) as conn:
                return db.migrate(conn)

    for i in range(1, attempts + 1):
        try:
            applied = migrate(dsn)
            log.info("migrations: %s", ", ".join(applied) if applied else "up to date")
            return applied
        except psycopg.OperationalError as exc:
            log.warning("database not ready (%s), attempt %d/%d", type(exc).__name__, i, attempts)
            if i < attempts:
                sleep(delay_s)
    log.error("could not apply migrations; starting without a guaranteed schema")
    return None


def uvicorn_log_config() -> dict:
    """uvicorn's default logging, but everything on stdout (platforms tag stderr as errors)."""
    from uvicorn.config import LOGGING_CONFIG

    cfg = copy.deepcopy(LOGGING_CONFIG)
    for handler in cfg["handlers"].values():
        handler["stream"] = "ext://sys.stdout"
    return cfg


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    dsn = os.environ.get("DATABASE_URL") or None
    if dsn and os.environ.get("CANESAT_MIGRATE_ON_START", "1") not in ("0", "false"):
        migrate_with_retry(dsn)

    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    log.info("starting uvicorn on 0.0.0.0:%d", port)
    uvicorn.run(
        "canesat.line.app:create_app",
        factory=True,
        host="0.0.0.0",
        port=port,
        log_level=os.environ.get("LOG_LEVEL", "info").lower(),
        log_config=uvicorn_log_config(),
        server_header=False,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )


if __name__ == "__main__":
    main()
