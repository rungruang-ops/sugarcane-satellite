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
    # optional link targets used in replies (LIFF does not exist yet)
    liff_url: str | None = field(default_factory=lambda: _env("LIFF_URL"))

    def __repr__(self) -> str:  # never leak secrets into logs / tracebacks
        def flag(v: str | None, none: str = "<missing>") -> str:
            return "<set>" if v else none

        return (
            f"LineSettings(channel_secret={flag(self.channel_secret)}, "
            f"channel_access_token={flag(self.channel_access_token)}, "
            f"database_url={flag(self.database_url, '<none>')})"
        )
