#!/usr/bin/env python
"""Create (or replace) the เบิ่งไฮ่ Rich Menu and set it as the default for all users.

    # render the image + print the JSON spec only (no API calls)
    python scripts/create_rich_menu.py
    # render, validate, create, upload image, set as default, delete our older menus
    python scripts/create_rich_menu.py --apply --secrets-json S.json

Idempotent: menus whose name starts with "bernghai-main" (created by this script) other than
the new one are deleted after the new default is set. Menus created by anyone else are kept.
"เพิ่มแปลง" opens https://liff.line.me/<LIFF_ID> when LIFF_ID is known (--liff-id, LIFF_ID in
the secrets JSON, or env); otherwise it sends the keyword "เพิ่มแปลง".
The channel access token is read from the secrets JSON / env and never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _secrets import load_optional, load_secrets  # noqa: E402

from canesat.line.richmenu import NAME_PREFIX, render_rich_menu_image, rich_menu_spec  # noqa: E402

API = "https://api.line.me/v2/bot"
API_DATA = "https://api-data.line.me/v2/bot"


def _req(method: str, url: str, token: str, data: bytes | None = None, ctype: str | None = None):
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if ctype:
        req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode() or "{}"
            return json.loads(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode()[:500]
        raise SystemExit(
            f"{method} {url.split('/v2/bot')[-1]} -> HTTP {exc.code}: {body}"
        ) from None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually call the LINE API")
    ap.add_argument("--secrets-json", default=os.environ.get("CANESAT_SECRETS_JSON"))
    ap.add_argument("--liff-id", default=None)
    ap.add_argument("--out", type=Path, default=ROOT / "docs" / "richmenu.png")
    ap.add_argument("--copy-to", type=Path, action="append", default=[])
    args = ap.parse_args()

    liff_id = (
        args.liff_id
        or load_optional(args.secrets_json, ["LIFF_ID"]).get("LIFF_ID")
        or os.environ.get("LIFF_ID")
    )
    spec = rich_menu_spec(liff_id)
    img = render_rich_menu_image(args.out)
    for dst in args.copy_to:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(img, dst)
    size = img.stat().st_size
    if size > 1_000_000:
        raise SystemExit(f"image too large for LINE ({size} bytes > 1 MB)")
    if not args.apply:
        print(json.dumps(spec, ensure_ascii=False, indent=2))
        print(f"image: {img} ({size} bytes); dry run — use --apply to create", file=sys.stderr)
        return

    if args.secrets_json:
        token = load_secrets(args.secrets_json, ["LINE_CHANNEL_ACCESS_TOKEN"])[
            "LINE_CHANNEL_ACCESS_TOKEN"
        ]
    else:
        token = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
    body = json.dumps(spec, ensure_ascii=False).encode()
    _req("POST", f"{API}/richmenu/validate", token, body, "application/json")
    rid = _req("POST", f"{API}/richmenu", token, body, "application/json")["richMenuId"]
    _req("POST", f"{API_DATA}/richmenu/{rid}/content", token, img.read_bytes(), "image/png")
    _req("POST", f"{API}/user/all/richmenu/{rid}", token)
    default = _req("GET", f"{API}/user/all/richmenu", token).get("richMenuId")

    deleted = []
    for m in _req("GET", f"{API}/richmenu/list", token).get("richmenus", []):
        if m["richMenuId"] != rid and m.get("name", "").startswith(NAME_PREFIX):
            _req("DELETE", f"{API}/richmenu/{m['richMenuId']}", token)
            deleted.append(m["richMenuId"])
    print(
        json.dumps(
            {
                "richMenuId": rid,
                "name": spec["name"],
                "is_default": default == rid,
                "add_plot_action": spec["areas"][0]["action"]["type"],
                "deleted_old": deleted,
                "image": str(img),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
