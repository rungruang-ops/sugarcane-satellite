"""Load named secrets from a JSON file (searched recursively by key). Values are never printed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def find_keys(data: Any, wanted: set[str]) -> dict[str, str]:
    found: dict[str, str] = {}

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                if k in wanted and isinstance(v, str) and v and k not in found:
                    found[k] = v
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return found


def load_secrets(path: str | Path, keys: list[str]) -> dict[str, str]:
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    found = find_keys(data, set(keys))
    missing = [k for k in keys if k not in found]
    if missing:
        raise SystemExit(f"secrets file is missing keys: {', '.join(missing)}")
    return found
