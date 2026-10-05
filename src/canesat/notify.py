"""Queue / send LINE push alerts behind ENABLE_PUSH_ALERTS (default OFF).

Reply messages stay free; push counts against the OA Free plan 300/month quota.
Never enable on production without an explicit Sam yes.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from psycopg.types.json import Jsonb

from .line import messages as m

log = logging.getLogger(__name__)
BANGKOK = ZoneInfo("Asia/Bangkok")

ALERT_PRIORITY = {"greenness": 0, "rain_gap": 1, "rain_back": 2, "harvest_check": 3}


def push_alerts_enabled() -> bool:
    return (os.environ.get("ENABLE_PUSH_ALERTS") or "false").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


class PushClient(Protocol):
    def push(self, line_user_id: str, messages: list[dict[str, Any]]) -> bool: ...


def quota_month(now: datetime | None = None) -> str:
    now = now or datetime.now(tz=BANGKOK)
    return f"{now.year:04d}-{now.month:02d}"


def count_pushes_this_month(conn, month: str | None = None) -> int:
    month = month or quota_month()
    row = conn.execute(
        "SELECT count(*) AS n FROM alert_deliveries"
        " WHERE kind = 'push' AND status = 'sent' AND quota_month = %s",
        (month,),
    ).fetchone()
    return int(row["n"])


def pending_alerts(conn, types: list[str] | None = None, limit: int = 100) -> list[dict[str, Any]]:
    types = types or ["greenness", "rain_gap", "rain_back"]
    return list(
        conn.execute(
            """
            SELECT a.*, p.name AS plot_name,
                   ST_Y(ST_Centroid(p.geom)) AS lat, ST_X(ST_Centroid(p.geom)) AS lon,
                   o.line_user_id AS owner_line_id, o.id AS owner_user_id,
                   o.has_water_source,
                   p.tambon_code
            FROM alerts a
            JOIN plots p ON p.id = a.plot_id
            LEFT JOIN users o ON o.id = p.owner_user_id
            WHERE a.status = 'pending' AND a.type = ANY(%s)
            ORDER BY CASE a.type
                WHEN 'greenness' THEN 0 WHEN 'rain_gap' THEN 1
                WHEN 'rain_back' THEN 2 ELSE 3 END,
                a.obs_date, a.id
            LIMIT %s
            """,
            (types, limit),
        )
    )


def recipients_for_alert(conn, alert: dict[str, Any]) -> list[dict[str, Any]]:
    """Owner (if active LINE user) + leaders with leader_gets_alerts on the plot's group."""
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    if alert.get("owner_line_id") and alert.get("owner_user_id"):
        row = conn.execute(
            "SELECT id, line_user_id, display_name, is_active FROM users WHERE id = %s",
            (alert["owner_user_id"],),
        ).fetchone()
        if row and row["is_active"] and row["line_user_id"]:
            out.append(dict(row))
            seen.add(int(row["id"]))
    # group leaders who may receive alerts
    leaders = conn.execute(
        """
        SELECT u.id, u.line_user_id, u.display_name, u.is_active
        FROM plots p
        JOIN group_members gm ON gm.group_id = p.group_id AND gm.user_id = p.owner_user_id
        JOIN farmer_groups g ON g.id = p.group_id
        JOIN users u ON u.id = g.leader_user_id
        WHERE p.id = %s AND gm.leader_gets_alerts AND u.is_active AND u.line_user_id IS NOT NULL
        """,
        (alert["plot_id"],),
    )
    for row in leaders:
        if int(row["id"]) not in seen:
            out.append(dict(row))
            seen.add(int(row["id"]))
    return out


def build_alert_messages(alert: dict[str, Any], *, graph_url: str | None = None) -> list[dict]:
    t = alert["type"]
    if t == "greenness":
        return [
            m.build_greenness_alert_flex(
                alert_id=int(alert["id"]),
                plot_name=alert["plot_name"] or "แปลง",
                obs_date=alert["obs_date"],
                lat=alert.get("lat"),
                lon=alert.get("lon"),
                graph_url=graph_url,
            )
        ]
    if t == "rain_gap":
        details = alert.get("details") or {}
        return [
            m.build_rain_gap_flex(
                alert_id=int(alert["id"]),
                dry_days=int(details.get("dry_days") or 0),
                tambon=alert.get("tambon_code"),
                guidance=details.get("guidance") or [],
                soil_moisture=details.get("soil_moisture"),
            )
        ]
    if t == "rain_back":
        details = alert.get("details") or {}
        return [
            m.build_rain_back_flex(
                precip_3d_mm=float(details.get("precip_3d_mm") or 0),
                tambon=alert.get("tambon_code"),
            )
        ]
    return [m.text(f"แจ้งเตือน ({t}) แปลง {alert.get('plot_name')}")]


def deliver_pending(
    conn,
    push: PushClient | None,
    *,
    enable_push: bool | None = None,
    monthly_quota: int = 280,
    types: list[str] | None = None,
    graph_base_url: str | None = None,
) -> dict[str, Any]:
    """Process pending alerts. When push is disabled, write dry_run deliveries only."""
    enable = push_alerts_enabled() if enable_push is None else enable_push
    month = quota_month()
    used = count_pushes_this_month(conn, month)
    report: dict[str, Any] = {
        "enable_push_alerts": enable,
        "quota_month": month,
        "pushes_used": used,
        "monthly_quota": monthly_quota,
        "delivered": [],
        "dry_run": [],
        "skipped": [],
    }
    alerts = pending_alerts(conn, types=types)
    for alert in alerts:
        recipients = recipients_for_alert(conn, alert)
        if not recipients:
            report["skipped"].append({"alert_id": alert["id"], "reason": "no_recipients"})
            continue
        graph_url = None
        if graph_base_url and alert.get("plot_id"):
            graph_url = f"{graph_base_url.rstrip('/')}/liff/chart?plot_id={alert['plot_id']}"
        messages = build_alert_messages(alert, graph_url=graph_url)
        for user in recipients:
            entry = {
                "alert_id": int(alert["id"]),
                "user_id": int(user["id"]),
                "line_user_id": user["line_user_id"],
                "type": alert["type"],
                "plot_id": alert["plot_id"],
            }
            if not enable or push is None:
                _record_delivery(
                    conn, alert["id"], user["id"], "dry_run", "skipped", month,
                    {"reason": "push_disabled"},
                )
                report["dry_run"].append(entry)
                continue
            if used >= monthly_quota:
                _record_delivery(
                    conn, alert["id"], user["id"], "push", "skipped", month,
                    {"reason": "quota"},
                )
                report["skipped"].append({**entry, "reason": "quota"})
                continue
            ok = push.push(user["line_user_id"], messages)
            if ok:
                _record_delivery(conn, alert["id"], user["id"], "push", "sent", month, {})
                conn.execute(
                    "UPDATE alerts SET status = 'sent', sent_at = now() WHERE id = %s",
                    (alert["id"],),
                )
                used += 1
                report["delivered"].append(entry)
            else:
                _record_delivery(
                    conn, alert["id"], user["id"], "push", "failed", month, {}
                )
                report["skipped"].append({**entry, "reason": "push_failed"})
        # mark pending→suppressed dry-run path: leave as pending so a later enable can send
        if enable and recipients and any(
            d["alert_id"] == alert["id"] for d in report["delivered"]
        ):
            pass
        elif not enable:
            # keep status=pending; dry_run only
            pass
    conn.commit()
    report["pushes_used"] = used
    return report


def _record_delivery(conn, alert_id, user_id, kind, status, month, detail) -> None:
    conn.execute(
        """
        INSERT INTO alert_deliveries (alert_id, user_id, kind, status, quota_month, detail, sent_at)
        VALUES (%s, %s, %s, %s, %s, %s, CASE WHEN %s = 'sent' THEN now() ELSE NULL END)
        """,
        (alert_id, user_id, kind, status, month, Jsonb(detail), status),
    )
