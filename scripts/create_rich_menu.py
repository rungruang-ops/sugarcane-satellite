#!/usr/bin/env python
"""Create the เบิ่งไฮ่ Rich Menu (6 buttons, design mockup 01). MANUAL STEP — not run by CI
or by the webhook.

    # print the JSON spec only (default, no API calls)
    python scripts/create_rich_menu.py
    # validate + create + upload image (2500x1686 PNG/JPEG < 1 MB) + set as default
    python scripts/create_rich_menu.py --apply --image richmenu.png --secrets-json S.json

The token is read from the secrets JSON / LINE_CHANNEL_ACCESS_TOKEN and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _secrets import load_secrets

from canesat.line.richmenu import rich_menu_spec


def _req(method: str, url: str, token: str, data: bytes | None, ctype: str | None) -> dict:
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode() or "{}"
        return json.loads(raw)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually call the LINE API")
    ap.add_argument("--image", type=Path)
    ap.add_argument("--secrets-json", default=os.environ.get("CANESAT_SECRETS_JSON"))
    args = ap.parse_args()

    spec = rich_menu_spec()
    if not args.apply:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        return
    if not args.image or not args.image.exists():
        ap.error("--image is required with --apply")
    token = (
        load_secrets(args.secrets_json, ["LINE_CHANNEL_ACCESS_TOKEN"])["LINE_CHANNEL_ACCESS_TOKEN"]
        if args.secrets_json
        else os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    )
    body = json.dumps(spec).encode()
    api = "https://api.line.me/v2/bot/richmenu"
    _req("POST", f"{api}/validate", token, body, "application/json")
    rid = _req("POST", api, token, body, "application/json")["richMenuId"]
    ctype = "image/png" if args.image.suffix.lower() == ".png" else "image/jpeg"
    _req(
        "POST",
        f"https://api-data.line.me/v2/bot/richmenu/{rid}/content",
        token,
        args.image.read_bytes(),
        ctype,
    )
    _req("POST", f"https://api.line.me/v2/bot/user/all/richmenu/{rid}", token, None, None)
    print(json.dumps({"richMenuId": rid, "default": True}))


if __name__ == "__main__":
    main()
