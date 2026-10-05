"""Runtime settings for the LINE webhook, read from the process environment.

Secrets are never logged: ``repr()`` masks them.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value or None


@dataclass(frozen=True)
class LineSettings:
    channel_secret: str | None = field(default_factory=lambda: _env("LINE_CHANNEL_SECRET"))
    channel_access_token: str | None = field(
        default_factory=lambda: _env("LINE_CHANNEL_ACCESS_TOKEN"), repr=False
    )
    database_url: str | None = field(default_factory=lambda: _env("DATABASE_URL"))
    # LIFF app (plot registration page). LIFF_ID is not a secret; empty until the LIFF app exists.
    liff_id: str | None = field(default_factory=lambda: _env("LIFF_ID"))
    # LINE Login channel that owns the LIFF app = expected `aud` of LIFF ID tokens
    login_channel_id: str = field(
        default_factory=lambda: _env("LINE_LOGIN_CHANNEL_ID") or "2011859249"
    )
    # run NDVI backfill for newly registered plots in a background thread
    ingest_on_register: bool = field(
        default_factory=lambda: (_env("CANESAT_INGEST_ON_REGISTER") or "1") not in ("0", "false")
    )
    # Push alerts OFF by default (LINE OA Free = 300 msgs/month). Never enable on Railway
    # without an explicit Sam yes.
    enable_push_alerts: bool = field(
        default_factory=lambda: (_env("ENABLE_PUSH_ALERTS") or "false").lower()
        in ("1", "true", "yes", "on")
    )

    @property
    def liff_url(self) -> str | None:
        return f"https://liff.line.me/{self.liff_id}" if self.liff_id else None

    def __repr__(self) -> str:  # never leak secrets into logs / tracebacks
        def flag(v: str | None, none: str = "<missing>") -> str:
            return "<set>" if v else none

        return (
            f"LineSettings(channel_secret={flag(self.channel_secret)}, "
            f"channel_access_token={flag(self.channel_access_token)}, "
            f"database_url={flag(self.database_url, '<none>')}, "
            f"liff_id={flag(self.liff_id, 'None')})"
        )
