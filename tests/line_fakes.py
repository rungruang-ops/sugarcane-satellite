"""Shared fakes and helpers for the LINE webhook tests (no network, no real secrets)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any

TEST_SECRET = "0123456789abcdef0123456789abcdef"  # dummy, not a real channel secret
USER_ID = "U" + "a" * 32


def sign(body: str, secret: str = TEST_SECRET) -> str:
    digest = hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _base(event_type: str, **extra: Any) -> dict[str, Any]:
    ev = {
        "type": event_type,
        "mode": "active",
        "timestamp": 1759626000000,
        "source": {"type": "user", "userId": USER_ID},
        "webhookEventId": "01HZZZZZZZZZZZZZZZZZZZZZZZ",
        "deliveryContext": {"isRedelivery": False},
    }
    ev.update(extra)
    return ev


def follow_event() -> dict[str, Any]:
    return _base("follow", replyToken="rt-follow", follow={"isUnblocked": False})


def unfollow_event() -> dict[str, Any]:
    return _base("unfollow")


def text_event(text: str) -> dict[str, Any]:
    return _base(
        "message",
        replyToken="rt-text",
        message={"type": "text", "id": "1", "text": text, "quoteToken": "q"},
    )


def sticker_event() -> dict[str, Any]:
    return _base(
        "message",
        replyToken="rt-sticker",
        message={
            "type": "sticker",
            "id": "2",
            "packageId": "1",
            "stickerId": "1",
            "stickerResourceType": "STATIC",
            "quoteToken": "q",
        },
    )


def postback_event(data: str) -> dict[str, Any]:
    return _base("postback", replyToken="rt-postback", postback={"data": data})


def payload(*events: dict[str, Any]) -> str:
    return json.dumps({"destination": "Uxxxxxxxx", "events": list(events)}, ensure_ascii=False)


class FakeLine:
    def __init__(self, display_name: str | None = "สมาน") -> None:
        self.replies: list[tuple[str, list[dict[str, Any]]]] = []
        self.name = display_name

    def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> bool:
        self.replies.append((reply_token, messages))
        return True

    def display_name(self, user_id: str) -> str | None:
        return self.name


class FakeStore:
    def __init__(self, plots: list[str] | None = None, feedback_status: str = "recorded"):
        self.calls: list[tuple] = []
        self.plots = plots
        self.feedback_status = feedback_status

    def status(self) -> str:
        return "fake"

    def upsert_follow(self, line_user_id: str, display_name: str | None, at: datetime) -> bool:
        self.calls.append(("follow", line_user_id, display_name, at))
        return True

    def mark_unfollow(self, line_user_id: str, at: datetime) -> bool:
        self.calls.append(("unfollow", line_user_id, at))
        return True

    def record_feedback(self, alert_id, answer, line_user_id, at):
        self.calls.append(("feedback", alert_id, answer, line_user_id))
        return self.feedback_status

    def plot_names(self, line_user_id: str):
        self.calls.append(("plots", line_user_id))
        return self.plots
