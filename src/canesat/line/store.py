"""Persistence for the webhook: LINE users (follow/unfollow) and alert feedback.

The webhook must keep replying even when PostGIS is down, so every operation degrades to
"unavailable" instead of raising, and a failed connection is not retried for a short while.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

FeedbackStatus = Literal["recorded", "not_found", "unavailable"]


class Store(Protocol):
    def status(self) -> str: ...

    def upsert_follow(self, line_user_id: str, display_name: str | None, at: datetime) -> bool: ...

    def mark_unfollow(self, line_user_id: str, at: datetime) -> bool: ...

    def record_feedback(
        self, alert_id: int, answer: str, line_user_id: str | None, at: datetime
    ) -> FeedbackStatus: ...

    def plot_names(self, line_user_id: str) -> list[str] | None: ...


class NullStore:
    """No database configured: nothing is persisted."""

    def status(self) -> str:
        return "disabled"

    def upsert_follow(self, line_user_id: str, display_name: str | None, at: datetime) -> bool:
        return False

    def mark_unfollow(self, line_user_id: str, at: datetime) -> bool:
        return False

    def record_feedback(
        self, alert_id: int, answer: str, line_user_id: str | None, at: datetime
    ) -> FeedbackStatus:
        return "unavailable"

    def plot_names(self, line_user_id: str) -> list[str] | None:
        return None


DEFAULT_DISPLAY_NAME = "ผู้ใช้ LINE"


class PgStore:
    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: int = 3,
        retry_after_s: float = 30.0,
        connect_kwargs: dict[str, Any] | None = None,
    ) -> None:
        self._dsn = dsn
        self._timeout = connect_timeout
        self._retry_after = retry_after_s
        self._kwargs = connect_kwargs or {}
        self._down_until = 0.0

    # -------------------------------------------------------------- plumbing
    def _connect(self) -> psycopg.Connection | None:
        if time.monotonic() < self._down_until:
            return None
        try:
            return psycopg.connect(
                self._dsn, connect_timeout=self._timeout, row_factory=dict_row, **self._kwargs
            )
        except psycopg.Error as exc:
            self._down_until = time.monotonic() + self._retry_after
            log.warning("database unavailable (%s); continuing without DB", type(exc).__name__)
            return None

    def _run(self, fn, default):
        conn = self._connect()
        if conn is None:
            return default
        try:
            with conn:
                return fn(conn)
        except psycopg.Error as exc:
            log.warning("database error (%s): %s", type(exc).__name__, exc.diag.message_primary)
            return default
        finally:
            conn.close()

    # -------------------------------------------------------------- API
    def status(self) -> str:
        return self._run(lambda c: c.execute("SELECT 1").fetchone() and "ok", "unavailable")

    def upsert_follow(self, line_user_id: str, display_name: str | None, at: datetime) -> bool:
        def op(c: psycopg.Connection) -> bool:
            c.execute(
                """
                INSERT INTO users (line_user_id, display_name, followed_at, unfollowed_at,
                                   is_active)
                VALUES (%(uid)s, %(name)s, %(at)s, NULL, TRUE)
                ON CONFLICT (line_user_id) DO UPDATE SET
                    display_name  = CASE WHEN %(have_name)s THEN EXCLUDED.display_name
                                         ELSE users.display_name END,
                    followed_at   = EXCLUDED.followed_at,
                    unfollowed_at = NULL,
                    is_active     = TRUE
                """,
                {
                    "uid": line_user_id,
                    "name": display_name or DEFAULT_DISPLAY_NAME,
                    "have_name": bool(display_name),
                    "at": at,
                },
            )
            return True

        return self._run(op, False)

    def mark_unfollow(self, line_user_id: str, at: datetime) -> bool:
        def op(c: psycopg.Connection) -> bool:
            cur = c.execute(
                "UPDATE users SET is_active = FALSE, unfollowed_at = %s WHERE line_user_id = %s",
                (at, line_user_id),
            )
            return cur.rowcount > 0

        return self._run(op, False)

    def record_feedback(
        self, alert_id: int, answer: str, line_user_id: str | None, at: datetime
    ) -> FeedbackStatus:
        def op(c: psycopg.Connection) -> FeedbackStatus:
            row = c.execute(
                """
                UPDATE alerts SET feedback = %(answer)s, feedback_at = %(at)s,
                    feedback_user_id = (SELECT id FROM users WHERE line_user_id = %(uid)s)
                WHERE id = %(alert_id)s
                RETURNING id
                """,
                {"answer": answer, "at": at, "uid": line_user_id, "alert_id": alert_id},
            ).fetchone()
            return "recorded" if row else "not_found"

        return self._run(op, "unavailable")

    def plot_names(self, line_user_id: str) -> list[str] | None:
        def op(c: psycopg.Connection) -> list[str]:
            rows = c.execute(
                """
                SELECT p.name, p.area_rai FROM plots p JOIN users u ON u.id = p.owner_user_id
                WHERE u.line_user_id = %s AND p.archived_at IS NULL ORDER BY p.id
                """,
                (line_user_id,),
            )
            return [
                f"{r['name']} (ประมาณ {float(r['area_rai']):.0f} ไร่)"
                if r["area_rai"] is not None
                else r["name"]
                for r in rows
            ]

        return self._run(op, None)
