#!/usr/bin/env python
"""Set / test / show the LINE Messaging API webhook endpoint.

    python scripts/line_webhook_endpoint.py --secrets-json S.json set https://x.example/callback
    python scripts/line_webhook_endpoint.py --secrets-json S.json test
    python scripts/line_webhook_endpoint.py --secrets-json S.json get

The channel access token is read from the JSON file (or LINE_CHANNEL_ACCESS_TOKEN) and only
sent in the Authorization header; it is never printed. Responses are printed as JSON.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _secrets import load_secrets

API = "https://api.line.me/v2/bot/channel/webhook"


def call(method: str, url: str, token: str, payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode() or "{}"
            return resp.status, json.loads(raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() or "{}"
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--secrets-json", default=os.environ.get("CANESAT_SECRETS_JSON"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set")
    s.add_argument("endpoint")
    t = sub.add_parser("test")
    t.add_argument("--endpoint", default=None, help="optional URL to test instead of current")
    sub.add_parser("get")
    args = ap.parse_args()

    if args.secrets_json:
        token = load_secrets(args.secrets_json, ["LINE_CHANNEL_ACCESS_TOKEN"])[
            "LINE_CHANNEL_ACCESS_TOKEN"
        ]
    else:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        raise SystemExit("no channel access token")

    if args.cmd == "set":
        status, body = call("PUT", f"{API}/endpoint", token, {"endpoint": args.endpoint})
    elif args.cmd == "test":
        payload = {"endpoint": args.endpoint} if args.endpoint else {}
        status, body = call("POST", f"{API}/test", token, payload)
    else:
        status, body = call("GET", f"{API}/endpoint", token)
    print(json.dumps({"http_status": status, "response": body}, ensure_ascii=False, indent=2))
    sys.exit(0 if 200 <= status < 300 else 1)


if __name__ == "__main__":
    main()
