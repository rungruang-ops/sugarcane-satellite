"""Verify LIFF ID tokens with LINE (POST https://api.line.me/oauth2/v2.1/verify)."""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Protocol

log = logging.getLogger(__name__)

VERIFY_URL = "https://api.line.me/oauth2/v2.1/verify"
ISSUER = "https://access.line.me"


class InvalidIdToken(Exception):
    pass


@dataclass(frozen=True)
class LineIdentity:
    user_id: str
    name: str | None = None


class IdTokenVerifier(Protocol):
    def verify(self, id_token: str) -> LineIdentity: ...


def _post_form(url: str, data: dict[str, str], timeout: float) -> tuple[int, dict[str, Any]]:
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode() or "{}")
        except json.JSONDecodeError:
            return exc.code, {}


class LineIdTokenVerifier:
    """Calls LINE's verify endpoint; caches successful results until the token expires."""

    def __init__(
        self, client_id: str, *, timeout: float = 8.0, post=_post_form, cache_size: int = 512
    ) -> None:
        self.client_id = client_id
        self.timeout = timeout
        self._post = post
        self._cache: OrderedDict[str, tuple[float, LineIdentity]] = OrderedDict()
        self._cache_size = cache_size

    def verify(self, id_token: str) -> LineIdentity:
        if not id_token or len(id_token) > 4096:
            raise InvalidIdToken("missing token")
        key = hashlib.sha256(id_token.encode()).hexdigest()
        hit = self._cache.get(key)
        now = time.time()
        if hit and hit[0] > now:
            return hit[1]
        status, body = self._post(
            VERIFY_URL, {"id_token": id_token, "client_id": self.client_id}, self.timeout
        )
        if status != 200:
            log.info("ID token rejected by LINE: HTTP %s %s", status, body.get("error", ""))
            raise InvalidIdToken(body.get("error_description") or "invalid token")
        if body.get("iss") != ISSUER or str(body.get("aud")) != str(self.client_id):
            raise InvalidIdToken("wrong issuer/audience")
        sub = body.get("sub")
        if not isinstance(sub, str) or not sub.startswith("U"):
            raise InvalidIdToken("no subject")
        exp = float(body.get("exp") or now + 60)
        ident = LineIdentity(user_id=sub, name=body.get("name"))
        self._cache[key] = (min(exp, now + 3600), ident)
        while len(self._cache) > self._cache_size:
            self._cache.popitem(last=False)
        return ident
