"""Thin wrapper over line-bot-sdk v3 MessagingApi.

Reply is always available. ``push`` exists for the notify job but must stay gated
behind ENABLE_PUSH_ALERTS (default off) — push counts against the OA Free 300/month
quota.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    PushMessageRequest,
    ReplyMessageRequest,
)
from linebot.v3.messaging.exceptions import ApiException

log = logging.getLogger(__name__)


class LineApi(Protocol):
    def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> bool: ...

    def display_name(self, user_id: str) -> str | None: ...

    def push(self, user_id: str, messages: list[dict[str, Any]]) -> bool: ...


class LineClient:
    def __init__(self, channel_access_token: str) -> None:
        if not channel_access_token:
            raise ValueError("LINE channel access token is required")
        self._api_client = ApiClient(Configuration(access_token=channel_access_token))
        self._api = MessagingApi(self._api_client)

    def reply(self, reply_token: str, messages: list[dict[str, Any]]) -> bool:
        req = ReplyMessageRequest.from_dict({"replyToken": reply_token, "messages": messages})
        try:
            self._api.reply_message(req)
            return True
        except ApiException as exc:  # log status/body only, never request headers
            log.warning("reply failed: HTTP %s %s", exc.status, (exc.body or "")[:300])
            return False

    def display_name(self, user_id: str) -> str | None:
        try:
            return self._api.get_profile(user_id).display_name
        except ApiException as exc:
            log.info("profile lookup failed: HTTP %s", exc.status)
            return None


    def push(self, user_id: str, messages: list[dict[str, Any]]) -> bool:
        """Push a message. Caller must check ENABLE_PUSH_ALERTS before invoking."""
        req = PushMessageRequest.from_dict({"to": user_id, "messages": messages})
        try:
            self._api.push_message(req)
            return True
        except ApiException as exc:
            log.warning("push failed: HTTP %s %s", exc.status, (exc.body or "")[:300])
            return False

    def close(self) -> None:
        self._api_client.close()
