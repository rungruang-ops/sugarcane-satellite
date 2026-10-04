"""Webhook event routing: follow / unfollow / text message / postback -> reply messages."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

from linebot.v3.webhooks import (
    FollowEvent,
    MessageEvent,
    PostbackEvent,
    TextMessageContent,
    UnfollowEvent,
)

from . import messages as m
from .client import LineApi
from .store import NullStore, Store

log = logging.getLogger(__name__)

Replies = list[dict[str, Any]]

# keyword -> reply builder. Matched exactly first, then as a substring (in this order).
KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("เมนู", "help", "menu", "วิธีใช้", "ช่วยเหลือ", "ติดต่อ", "?", "สวัสดี", "hello"), "help"),
    (("แปลงของฉัน", "แปลงของผม", "แปลงของหนู", "my plot", "plots"), "my_plots"),
    (("เทียบเพื่อนบ้าน", "เทียบ", "เพื่อนบ้าน", "กราฟ", "compare"), "compare"),
    (("เพิ่มแปลง", "ลงทะเบียน", "register"), "add_plot"),
    (("หัวหน้ากลุ่ม", "กลุ่ม", "leader"), "leader"),
    (("ฝน", "ความชื้น", "แล้ง", "ปุ๋ย", "rain"), "rain"),
]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def match_keyword(text: str) -> str | None:
    t = _normalize(text)
    if not t:
        return None
    for words, intent in KEYWORDS:
        if t in words:
            return intent
    for words, intent in KEYWORDS:
        if any(len(w) >= 2 and w in t for w in words):
            return intent
    return None


def _event_time(event: Any) -> datetime:
    ts = getattr(event, "timestamp", None)
    if ts:
        return datetime.fromtimestamp(ts / 1000, tz=UTC)
    return datetime.now(tz=UTC)


def _user_id(event: Any) -> str | None:
    return getattr(getattr(event, "source", None), "user_id", None)


def _short(uid: str | None) -> str:
    return f"{uid[:5]}…" if uid else "-"


class EventRouter:
    def __init__(
        self, line: LineApi, store: Store | None = None, liff_url: str | None = None
    ) -> None:
        self.line = line
        self.store: Store = store or NullStore()
        self.liff_url = liff_url
        self._handlers: list[tuple[type, Callable[[Any], Replies | None]]] = [
            (FollowEvent, self.on_follow),
            (UnfollowEvent, self.on_unfollow),
            (MessageEvent, self.on_message),
            (PostbackEvent, self.on_postback),
        ]

    # -------------------------------------------------------------- dispatch
    def dispatch_all(self, events: Iterable[Any]) -> None:
        for ev in events:
            try:
                self.dispatch(ev)
            except Exception:  # one bad event must not break the batch
                log.exception("error handling %s event", getattr(ev, "type", "?"))

    def dispatch(self, event: Any) -> Replies | None:
        for cls, fn in self._handlers:
            if isinstance(event, cls):
                replies = fn(event)
                break
        else:
            log.info("ignored event type=%s", getattr(event, "type", type(event).__name__))
            return None
        token = getattr(event, "reply_token", None)
        if replies and token:
            self.line.reply(token, replies)
        return replies

    # -------------------------------------------------------------- handlers
    def on_follow(self, event: FollowEvent) -> Replies:
        uid = _user_id(event)
        name = self.line.display_name(uid) if uid else None
        saved = self.store.upsert_follow(uid, name, _event_time(event)) if uid else False
        log.info("follow user=%s saved=%s", _short(uid), saved)
        return m.welcome(name)

    def on_unfollow(self, event: UnfollowEvent) -> None:
        uid = _user_id(event)
        saved = self.store.mark_unfollow(uid, _event_time(event)) if uid else False
        log.info("unfollow user=%s saved=%s", _short(uid), saved)
        return None  # no reply token on unfollow

    def on_message(self, event: MessageEvent) -> Replies:
        if not isinstance(event.message, TextMessageContent):
            return m.non_text_received()
        if event.message.text.strip().startswith(m.REGISTERED_PREFIX):
            log.info("text user=%s intent=registered_ack", _short(_user_id(event)))
            return m.registered_ack()
        intent = match_keyword(event.message.text)
        log.info("text user=%s intent=%s", _short(_user_id(event)), intent)
        if intent == "help":
            return m.help_menu()
        if intent == "my_plots":
            uid = _user_id(event)
            return m.my_plots(self.store.plot_names(uid) if uid else None, self.liff_url)
        if intent == "compare":
            return m.compare_neighbours()
        if intent == "add_plot":
            return m.add_plot(self.liff_url)
        if intent == "leader":
            return m.group_leader()
        if intent == "rain":
            return m.rain_info()
        return m.fallback()

    def on_postback(self, event: PostbackEvent) -> Replies | None:
        data = m.parse_postback_data(event.postback.data)
        action = data.get("action")
        uid = _user_id(event)
        if action == "feedback":
            answer = data.get("answer", "")
            try:
                alert_id = int(data.get("alert_id", ""))
            except ValueError:
                alert_id = None
            if answer not in m.FEEDBACK_CHOICES or alert_id is None:
                log.info("bad feedback postback from user=%s", _short(uid))
                return m.fallback()
            status = self.store.record_feedback(alert_id, answer, uid, _event_time(event))
            log.info("feedback alert=%s answer=%s status=%s", alert_id, answer, status)
            return m.feedback_thanks(answer, status)
        if action == "rain_report":
            answer = data.get("answer", "")
            if answer not in m.RAIN_REPORT_CHOICES:
                return m.fallback()
            log.info("rain_report user=%s answer=%s", _short(uid), answer)
            return m.rain_report_thanks(answer)
        log.info("unknown postback action=%s", action)
        return m.fallback()
