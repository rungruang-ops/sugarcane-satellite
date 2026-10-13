"""Notify job: dry-run by default, push only when enabled."""

from datetime import UTC, datetime

import pytest

from canesat import notify
from canesat.notify import push_alerts_enabled


class FakePush:
    def __init__(self):
        self.calls = []

    def push(self, user_id, messages):
        self.calls.append((user_id, messages))
        return True


def test_push_flag_defaults_off(monkeypatch):
    monkeypatch.delenv("ENABLE_PUSH_ALERTS", raising=False)
    assert push_alerts_enabled() is False
    monkeypatch.setenv("ENABLE_PUSH_ALERTS", "true")
    assert push_alerts_enabled() is True


def test_rain_flex_builders():
    from canesat.line.messages import build_rain_back_flex, build_rain_gap_flex

    gap = build_rain_gap_flex(alert_id=1, dry_days=16, guidance=["ถ้ามีน้ำ: ให้น้ำ"], soil_moisture=0.1)
    assert gap["type"] == "flex" and "16" in gap["altText"]
    back = build_rain_back_flex(precip_3d_mm=22.5)
    assert "ฝนกลับมา" in back["altText"]


# --- quiet hours / send window -------------------------------------------------------------


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [(5, 59, False), (6, 0, True), (12, 0, True), (18, 59, True), (19, 0, False), (23, 30, False)],
)
def test_within_send_window_default_boundaries(hour, minute, expected):
    now = datetime(2026, 10, 13, hour, minute)  # naive = Asia/Bangkok
    assert notify.within_send_window(now, (6, 19)) is expected


def test_within_send_window_converts_aware_datetimes_to_bangkok():
    # 23:00 UTC is 06:00 the next day in Bangkok (UTC+7) -> inside the window
    assert notify.within_send_window(datetime(2026, 10, 12, 23, 0, tzinfo=UTC), (6, 19))
    # 12:00 UTC is 19:00 in Bangkok -> end hour is exclusive
    assert not notify.within_send_window(datetime(2026, 10, 13, 12, 0, tzinfo=UTC), (6, 19))


def test_send_window_defaults_and_env_override(monkeypatch):
    monkeypatch.delenv("PUSH_WINDOW_START_HOUR", raising=False)
    monkeypatch.delenv("PUSH_WINDOW_END_HOUR", raising=False)
    assert notify.send_window() == (6, 19)
    monkeypatch.setenv("PUSH_WINDOW_START_HOUR", "8")
    monkeypatch.setenv("PUSH_WINDOW_END_HOUR", "17")
    assert notify.send_window() == (8, 17)


@pytest.mark.parametrize(
    ("start", "end"), [("x", "19"), ("6", ""), ("19", "6"), ("6", "6"), ("-1", "19"), ("6", "25")]
)
def test_send_window_invalid_values_fall_back_to_default(monkeypatch, start, end):
    monkeypatch.setenv("PUSH_WINDOW_START_HOUR", start)
    monkeypatch.setenv("PUSH_WINDOW_END_HOUR", end)
    assert notify.send_window() == (6, 19)


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class FakeConn:
    """Just enough of a psycopg connection for notify.deliver_pending."""

    def __init__(self, alerts, pushes_used=0):
        self.alerts = alerts
        self.pushes_used = pushes_used
        self.writes = []
        self.commits = 0

    def execute(self, sql, params=None):
        if "count(*)" in sql:
            return _Result([{"n": self.pushes_used}])
        if "FROM alerts a" in sql:
            return _Result(self.alerts)
        if "FROM users WHERE id" in sql:
            return _Result(
                [{"id": 7, "line_user_id": "Uowner", "display_name": "ลุง", "is_active": True}]
            )
        if sql.lstrip().startswith(("INSERT", "UPDATE")):
            self.writes.append(sql.split()[0])
        return _Result([])

    def commit(self):
        self.commits += 1


def _alert():
    return {
        "id": 1,
        "type": "rain_back",
        "plot_id": 3,
        "plot_name": "แปลงทดสอบ",
        "obs_date": "2026-10-13",
        "details": {"precip_3d_mm": 25},
        "owner_line_id": "Uowner",
        "owner_user_id": 7,
        "tambon_code": None,
    }


class _CountingPush:
    def __init__(self):
        self.calls = 0

    def push(self, user_id, messages):
        self.calls += 1
        return True


def test_deliver_pending_defers_push_outside_send_window():
    conn, push = FakeConn([_alert()]), _CountingPush()
    report = notify.deliver_pending(
        conn, push, enable_push=True, now=datetime(2026, 10, 13, 22, 30), window=(6, 19)
    )
    assert push.calls == 0
    assert conn.writes == []  # alert stays pending, nothing recorded
    assert report["in_send_window"] is False
    assert report["deferred"] == 1
    assert report["delivered"] == []


def test_deliver_pending_pushes_inside_send_window():
    conn, push = FakeConn([_alert()]), _CountingPush()
    report = notify.deliver_pending(
        conn, push, enable_push=True, now=datetime(2026, 10, 13, 9, 0), window=(6, 19)
    )
    assert push.calls == 1
    assert report["in_send_window"] is True
    assert report["deferred"] == 0
    assert len(report["delivered"]) == 1


def test_dry_run_is_not_gated_by_send_window():
    conn = FakeConn([_alert()])
    report = notify.deliver_pending(
        conn, None, enable_push=False, now=datetime(2026, 10, 13, 2, 0), window=(6, 19)
    )
    assert report["deferred"] == 0
    assert len(report["dry_run"]) == 1
