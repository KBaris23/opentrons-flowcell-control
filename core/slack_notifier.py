"""
core/slack_notifier.py - Minimal Slack notifier (chat.postMessage).

Used for:
- outbound notifications (queue started/ended, alerts, etc.)
"""

from __future__ import annotations

import json
import urllib.request
from typing import Callable, Optional


class SlackNotifier:
    """Send plain-text Slack notifications with a bot token."""

    _POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"

    def __init__(
        self,
        *,
        bot_token: str,
        default_target: str = "",
        log_callback: Callable[[str], None] = print,
    ) -> None:
        self._token = (bot_token or "").strip()
        self._default_target = (default_target or "").strip()
        self._log = log_callback

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    def send_message(self, message: str, *, target: Optional[str] = None) -> bool:
        if not self.enabled:
            return False

        channel = (target or self._default_target or "").strip()
        if not channel:
            self._log("Slack notify skipped: no target configured (EA_SLACK_TARGET).")
            return False

        payload = {"channel": channel, "text": str(message)}
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self._POST_MESSAGE_URL,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json; charset=utf-8",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read().decode("utf-8", errors="ignore")
            parsed = json.loads(raw) if raw else {}
            ok = bool(parsed.get("ok"))
            if not ok:
                self._log(f"Slack notify failed: {parsed.get('error', 'unknown_error')}")
            return ok
        except Exception as exc:
            self._log(f"Slack notify failed: {type(exc).__name__}: {exc}")
            return False

