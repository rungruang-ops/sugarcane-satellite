"""Persistence for the webhook: LINE users (follow/unfollow) and alert feedback.

The webhook must keep replying even when PostGIS is down, so every operation degrades to
"unavailable" instead of raising, and a failed connection is not retried for a short while.
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import psycopg
from psycopg.rows import dict_row

log = logging.getLogger(__name__)

FeedbackStatus = Literal["recorded", "not_found", "unavailable", "forbidden"]
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

    def confirm_consent(self, token: str, line_user_id: str, at: datetime) -> str: ...

    def pending_consents_for(self, line_user_id: str) -> list[dict[str, Any]]: ...

    def chart_series(
        self, plot_id: int, line_user_id: str | None = None
    ) -> dict[str, Any] | None: ...


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

    def confirm_consent(self, token: str, line_user_id: str, at: datetime) -> str:
        return "unavailable"

    def pending_consents_for(self, line_user_id: str) -> list[dict[str, Any]]:
        return []

    def chart_series(
        self, plot_id: int, line_user_id: str | None = None
    ) -> dict[str, Any] | None:
        return None

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
        """Only the plot owner or an authorised group leader may reply."""

        def op(c: psycopg.Connection) -> FeedbackStatus:
            if not line_user_id:
                return "forbidden"
            allowed = c.execute(
                """
                SELECT a.id,
                    (o.line_user_id = %(uid)s) AS is_owner,
                    EXISTS (
                        SELECT 1 FROM plots p2
                        JOIN farmer_groups g ON g.id = p2.group_id
                        JOIN users lead ON lead.id = g.leader_user_id
                        LEFT JOIN group_members gm
                          ON gm.group_id = g.id AND gm.user_id = p2.owner_user_id
                        WHERE p2.id = a.plot_id AND lead.line_user_id = %(uid)s
                          AND COALESCE(gm.leader_can_view, TRUE)
                    ) AS is_leader
                FROM alerts a
                LEFT JOIN plots p ON p.id = a.plot_id
                LEFT JOIN users o ON o.id = p.owner_user_id
                WHERE a.id = %(alert_id)s
                """,
                {"uid": line_user_id, "alert_id": alert_id},
            ).fetchone()
            if not allowed:
                return "not_found"
            if not (allowed["is_owner"] or allowed["is_leader"]):
                return "forbidden"
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

            on_behalf = bool(reg.get("on_behalf"))
            method = "assisted_pending" if on_behalf else "liff"
            confirm_token = secrets.token_urlsafe(24) if on_behalf else None
            for purpose in CONSENT_PURPOSES:
                granted = bool(reg["consents"].get(purpose))
                # member must confirm service consent themselves (design section 6.1)
                if on_behalf and purpose == "service":
                    granted = False
                confirmed_at = None if on_behalf else datetime.now(tz=UTC)
                c.execute(
                    "INSERT INTO consents (user_id, purpose, granted, version, method,"
                    " assisted_by, plot_id, confirm_token, confirmed_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        owner,
                        purpose,
                        granted,
                        reg["consent_version"],
                        method,
                        me if on_behalf else None,
                        plot_id,
                        confirm_token if purpose == "service" and on_behalf else None,
                        confirmed_at,
                    ),
                )
            return {
                "id": int(plot_id),
                "name": reg["name"],
                "area_rai": float(reg["area_rai"]),
                "owner": "member" if reg.get("on_behalf") else "self",
                "phone_stored": phone_stored,
                "consent_token": confirm_token,
                "consent_pending": bool(reg.get("on_behalf")),
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

    def confirm_consent(self, token: str, line_user_id: str, at: datetime) -> str:
        """Member confirms a leader-assisted registration (design section 6.1)."""

        def op(c: psycopg.Connection) -> str:
            row = c.execute(
                """
                SELECT c.id, c.user_id, c.plot_id, c.confirmed_at, u.line_user_id AS owner_line
                FROM consents c
                JOIN users u ON u.id = c.user_id
                WHERE c.confirm_token = %s AND c.purpose = 'service'
                """,
                (token,),
            ).fetchone()
            if not row:
                return "not_found"
            if row["confirmed_at"] is not None:
                return "recorded"
            me = c.execute(
                """
                INSERT INTO users (line_user_id, display_name) VALUES (%s, %s)
                ON CONFLICT (line_user_id) DO UPDATE SET display_name = users.display_name
                RETURNING id
                """,
                (line_user_id, DEFAULT_DISPLAY_NAME),
            ).fetchone()["id"]
            if row["owner_line"] and row["owner_line"] != line_user_id:
                return "forbidden"
            owner_id = int(row["user_id"])
            if not row["owner_line"]:
                c.execute(
                    "UPDATE plots SET owner_user_id = %s WHERE id = %s AND owner_user_id = %s",
                    (me, row["plot_id"], owner_id),
                )
                c.execute(
                    "UPDATE consents SET user_id = %s WHERE plot_id = %s AND user_id = %s",
                    (me, row["plot_id"], owner_id),
                )
                owner_id = me
            c.execute(
                """
                UPDATE consents SET granted = TRUE, confirmed_at = %s, method = 'line'
                WHERE plot_id = %s AND user_id = %s AND purpose = 'service'
                """,
                (at, row["plot_id"], owner_id),
            )
            return "recorded"

        return self._run(op, "unavailable")

    def pending_consents_for(self, line_user_id: str) -> list[dict[str, Any]]:
        def op(c: psycopg.Connection) -> list[dict[str, Any]]:
            rows = c.execute(
                """
                SELECT c.confirm_token, c.plot_id, p.name AS plot_name, c.at,
                       a.display_name AS assisted_by_name
                FROM consents c
                JOIN plots p ON p.id = c.plot_id
                LEFT JOIN users a ON a.id = c.assisted_by
                WHERE c.purpose = 'service' AND c.method = 'assisted_pending'
                  AND c.confirmed_at IS NULL AND c.confirm_token IS NOT NULL
                  AND a.line_user_id = %s
                ORDER BY c.at DESC
                LIMIT 20
                """,
                (line_user_id,),
            )
            return [
                {
                    "token": r["confirm_token"],
                    "plot_id": int(r["plot_id"]),
                    "plot_name": r["plot_name"],
                    "assisted_by": r["assisted_by_name"],
                    "at": _iso(r["at"]),
                }
                for r in rows
            ]

        return self._run(op, [])

    def chart_series(
        self, plot_id: int, line_user_id: str | None = None
    ) -> dict[str, Any] | None:
        def op(c: psycopg.Connection) -> dict[str, Any] | None:
            plot = c.execute(
                """
                SELECT p.id, p.name, p.area_rai, o.line_user_id AS owner_line,
                       r.line_user_id AS registrar_line,
                       ST_Y(ST_Centroid(p.geom)) AS lat, ST_X(ST_Centroid(p.geom)) AS lon
                FROM plots p
                LEFT JOIN users o ON o.id = p.owner_user_id
                LEFT JOIN users r ON r.id = p.registered_by
                WHERE p.id = %s AND p.archived_at IS NULL
                """,
                (plot_id,),
            ).fetchone()
            if not plot:
                return None
            if not line_user_id:
                return None
            if line_user_id not in (plot["owner_line"], plot["registrar_line"]):
                ok = c.execute(
                    """
                    SELECT 1 FROM plots p
                    JOIN farmer_groups g ON g.id = p.group_id
                    JOIN users lead ON lead.id = g.leader_user_id
                    WHERE p.id = %s AND lead.line_user_id = %s
                    """,
                    (plot_id, line_user_id),
                ).fetchone()
                if not ok:
                    return None
            rows = c.execute(
                """
                SELECT obs_date, median_ndvi, ring_median, nb_median, status, gap_nb, z_nb, z_hist
                FROM ndvi_observations
                WHERE plot_id = %s AND is_clear AND median_ndvi IS NOT NULL
                ORDER BY obs_date
                """,
                (plot_id,),
            )
            series = []
            for r in rows:
                series.append(
                    {
                        "date": r["obs_date"].isoformat(),
                        "ndvi": float(r["median_ndvi"]),
                        "neighbour": (
                            float(r["nb_median"])
                            if r["nb_median"] is not None
                            else (
                                float(r["ring_median"]) if r["ring_median"] is not None else None
                            )
                        ),
                        "status": r["status"],
                        "gap_nb": float(r["gap_nb"]) if r["gap_nb"] is not None else None,
                        "z_nb": float(r["z_nb"]) if r["z_nb"] is not None else None,
                        "z_hist": float(r["z_hist"]) if r["z_hist"] is not None else None,
                    }
                )
            return {
                "plot": {
                    "id": int(plot["id"]),
                    "name": plot["name"],
                    "area_rai": float(plot["area_rai"]) if plot["area_rai"] is not None else None,
                    "lat": float(plot["lat"]),
                    "lon": float(plot["lon"]),
                },
                "series": series,
            }

        try:
            return self._run_strict(op)
        except StoreUnavailable:
            return None



def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None
