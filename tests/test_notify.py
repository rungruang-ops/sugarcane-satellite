"""Notify job: dry-run by default, push only when enabled."""


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
