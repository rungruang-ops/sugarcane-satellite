#!/usr/bin/env python
"""Launch the เบิ่งไฮ่ LINE webhook (uvicorn) with LINE secrets loaded from a JSON file.

The secrets are put into the server *process environment* only (overriding any stale values
already in the shell) — never on the command line, never printed or logged.

    python scripts/run_webhook.py --secrets-json /path/to/secrets.json --port 8711

The JSON may nest the keys anywhere; LINE_CHANNEL_SECRET and LINE_CHANNEL_ACCESS_TOKEN are
found by name. DATABASE_URL is taken from --database-url or the environment (optional).
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _secrets import load_secrets

KEYS = ["LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN"]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--secrets-json", default=os.environ.get("CANESAT_SECRETS_JSON"))
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8711)
    ap.add_argument("--database-url", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--log-level", default="info")
    args = ap.parse_args()
    if not args.secrets_json:
        ap.error("--secrets-json (or CANESAT_SECRETS_JSON) is required")

    for k, v in load_secrets(args.secrets_json, KEYS).items():
        os.environ[k] = v  # overrides stale shell values
    if args.database_url:
        os.environ["DATABASE_URL"] = args.database_url
    print(
        f"[run_webhook] LINE secrets loaded from file; DB={'set' if args.database_url else 'none'};"
        f" listening on {args.host}:{args.port}",
        flush=True,
    )
    import logging

    import uvicorn

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    uvicorn.run(
        "canesat.line.app:create_app",
        factory=True,
        host=args.host,
        port=args.port,
        log_level=args.log_level,
        server_header=False,
        proxy_headers=True,
    )


if __name__ == "__main__":
    main()
