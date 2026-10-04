"""Persistence for the webhook: LINE users (follow/unfollow) and alert feedback.

The webhook must keep replying even when PostGIS is down, so every operation degrades to
"unavailable" instead of raising, and a failed connection is not retried for a short while.
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

FeedbackStatus = Literal["recorded", "not_found", "unavailable"]
MAX_PLOTS_PER_USER = 50


class StoreUnavailable(Exception):
    """Database not configured or unreachable."""


class PlotLimitReached(Exception):
    pass


CONSENT_PURPOSES = ("service", "leader_view", "research")


class Store(Protocol):
    def status(self) -> str: ...

    def upsert_follow(self, line_user_id: str, display_name: str | None, at: datetime) -> bool: ...

    def mark_unfollow(self, line_user_id: str, at: datetime) -> bool: ...

    def record_feedback(
        self, alert_id: int, answer: str, line_user_id: str | None, at: datetime
    ) -> FeedbackStatus: ...

    def plot_names(self, line_user_id: str) -> list[str] | None: ...

    def create_plot(self, reg: dict[str, Any]) -> dict[str, Any]: ...

    def list_plots(self, line_user_id: str) -> list[dict[str, Any]]: ...


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

    def create_plot(self, reg: dict[str, Any]) -> dict[str, Any]:
        raise StoreUnavailable("no database configured")

    def list_plots(self, line_user_id: str) -> list[dict[str, Any]]:
        raise StoreUnavailable("no database configured")


DEFAULT_DISPLAY_NAME = "ผู้ใช้ LINE"


class PgStore:
    def __init__(
        self,
        dsn: str,
        *,
        connect_timeout: int = 3,
        retry_after_s: float = 30.0,
        connect_kwargs: dict[str, Any] | None = None,
        phone_key: str | None = None,
    ) -> None:
        self._dsn = dsn
        self._phone_key = phone_key  # pgcrypto key for users.phone_enc; None = don't store phones
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

    def _run_strict(self, fn):
        """Like _run, but raises StoreUnavailable instead of returning a default."""
        conn = self._connect()
        if conn is None:
            raise StoreUnavailable("database unavailable")
        try:
            with conn:
                return fn(conn)
        except psycopg.OperationalError as exc:
            log.warning("database error (%s)", type(exc).__name__)
            raise StoreUnavailable("database error") from None
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
                SELECT p.name, p.area_rai, o.display_name AS owner_name,
                       (o.line_user_id IS NOT DISTINCT FROM %(uid)s) AS mine
                FROM plots p
                JOIN users o ON o.id = p.owner_user_id
                LEFT JOIN users r ON r.id = p.registered_by
                WHERE p.archived_at IS NULL
                  AND (o.line_user_id = %(uid)s OR r.line_user_id = %(uid)s)
                ORDER BY p.id
                """,
                {"uid": line_user_id},
            )
            out = []
            for r in rows:
                label = r["name"]
                if r["area_rai"] is not None:
                    label += f" (ประมาณ {float(r['area_rai']):.0f} ไร่)"
                if not r["mine"]:
                    label += f" — ของ{r['owner_name']}"
                out.append(label)
            return out

        return self._run(op, None)

    # -------------------------------------------------------------- LIFF plots
    def create_plot(self, reg: dict[str, Any]) -> dict[str, Any]:
        """Insert a plot registered from LIFF.

        ``reg`` keys: line_user_id, display_name, name, polygon (shapely, lon/lat), area_rai,
        cane_type, planting_date, last_harvest_date, on_behalf, member_name, member_phone,
        consents (dict purpose -> bool), consent_version.
        """
        from shapely.geometry import mapping

        def op(c: psycopg.Connection) -> dict[str, Any]:
            me = c.execute(
                """
                INSERT INTO users (line_user_id, display_name) VALUES (%s, %s)
                ON CONFLICT (line_user_id) DO UPDATE SET display_name = users.display_name
                RETURNING id
                """,
                (reg["line_user_id"], reg.get("display_name") or DEFAULT_DISPLAY_NAME),
            ).fetchone()["id"]
            n = c.execute(
                "SELECT count(*) AS n FROM plots WHERE registered_by = %s AND archived_at IS NULL",
                (me,),
            ).fetchone()["n"]
            if n >= MAX_PLOTS_PER_USER:
                raise PlotLimitReached()

            owner = me
            phone_stored = False
            if reg.get("on_behalf"):
                phone = reg.get("member_phone")
                if phone and self._phone_key:
                    owner = c.execute(
                        "INSERT INTO users (display_name, phone_enc)"
                        " VALUES (%s, pgp_sym_encrypt(%s, %s)) RETURNING id",
                        (reg["member_name"], phone, self._phone_key),
                    ).fetchone()["id"]
                    phone_stored = True
                else:
                    owner = c.execute(
                        "INSERT INTO users (display_name) VALUES (%s) RETURNING id",
                        (reg["member_name"],),
                    ).fetchone()["id"]

            plot_id = c.execute(
                """
                INSERT INTO plots (name, geom, area_rai, owner_user_id, registered_by, cane_type,
                                   planting_date, last_harvest_date, source)
                VALUES (%s, ST_Multi(ST_SetSRID(ST_GeomFromGeoJSON(%s), 4326)), %s, %s, %s, %s,
                        %s, %s, 'liff')
                RETURNING id
                """,
                (
                    reg["name"],
                    json.dumps(mapping(reg["polygon"])),
                    reg["area_rai"],
                    owner,
                    me,
                    reg["cane_type"],
                    reg.get("planting_date"),
                    reg.get("last_harvest_date"),
                ),
            ).fetchone()["id"]

            method = "assisted_pending" if reg.get("on_behalf") else "liff"
            for purpose in CONSENT_PURPOSES:
                c.execute(
                    "INSERT INTO consents (user_id, purpose, granted, version, method,"
                    " assisted_by, plot_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        owner,
                        purpose,
                        bool(reg["consents"].get(purpose)),
                        reg["consent_version"],
                        method,
                        me if reg.get("on_behalf") else None,
                        plot_id,
                    ),
                )
            return {
                "id": int(plot_id),
                "name": reg["name"],
                "area_rai": float(reg["area_rai"]),
                "owner": "member" if reg.get("on_behalf") else "self",
                "phone_stored": phone_stored,
            }

        return self._run_strict(op)

    def list_plots(self, line_user_id: str) -> list[dict[str, Any]]:
        def op(c: psycopg.Connection) -> list[dict[str, Any]]:
            rows = c.execute(
                """
                SELECT p.id, p.name, p.area_rai, p.cane_type, p.planting_date,
                       p.last_harvest_date, p.created_at, ST_AsGeoJSON(p.geom, 7) AS geometry,
                       o.display_name AS owner_name,
                       (o.line_user_id IS NOT DISTINCT FROM %(uid)s) AS mine,
                       (SELECT count(*) FROM ndvi_observations n
                         WHERE n.plot_id = p.id AND n.is_clear) AS n_clear_obs,
                       (SELECT max(obs_date) FROM ndvi_observations n
                         WHERE n.plot_id = p.id AND n.is_clear) AS last_clear_obs
                FROM plots p
                JOIN users o ON o.id = p.owner_user_id
                LEFT JOIN users r ON r.id = p.registered_by
                WHERE p.archived_at IS NULL
                  AND (o.line_user_id = %(uid)s OR r.line_user_id = %(uid)s)
                ORDER BY p.id DESC
                LIMIT 100
                """,
                {"uid": line_user_id},
            )
            out = []
            for r in rows:
                out.append(
                    {
                        "id": int(r["id"]),
                        "name": r["name"],
                        "area_rai": float(r["area_rai"]) if r["area_rai"] is not None else None,
                        "cane_type": r["cane_type"],
                        "planting_date": _iso(r["planting_date"]),
                        "last_harvest_date": _iso(r["last_harvest_date"]),
                        "created_at": _iso(r["created_at"]),
                        "geometry": json.loads(r["geometry"]),
                        "owner_name": None if r["mine"] else r["owner_name"],
                        "n_clear_obs": int(r["n_clear_obs"]),
                        "last_clear_obs": _iso(r["last_clear_obs"]),
                    }
                )
            return out

        return self._run_strict(op)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None
