"""
core/slack_bot.py - Slack Events API listener for status pings (full bot).

This module provides an inbound HTTP endpoint that Slack can call.
It validates request signatures using the Slack signing secret, and can reply
in-channel using SlackNotifier.
"""

from __future__ import annotations

import hmac
import hashlib
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional

from .slack_notifier import SlackNotifier


def _json_response(handler: BaseHTTPRequestHandler, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


class SlackBotServer:
    """Listen for Slack Events API app_mention and reply with queue status."""

    def __init__(
        self,
        *,
        host: str,
        port: int,
        signing_secret: str,
        notifier: SlackNotifier,
        status_provider: Callable[[], dict],
        log_callback: Callable[[str], None] = print,
    ) -> None:
        self._host = host
        self._port = int(port)
        self._secret = (signing_secret or "").strip().encode("utf-8")
        self._notifier = notifier
        self._status_provider = status_provider
        self._log = log_callback

        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._server is not None:
            return
        if not self._secret:
            self._log("Slack bot not started: missing signing secret (EA_SLACK_SIGNING_SECRET).")
            return

        server_self = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, _fmt: str, *_args) -> None:  # quiet default HTTP logs
                return

            def do_GET(self) -> None:
                if self.path == "/health":
                    _json_response(self, {"ok": True})
                    return
                _json_response(self, {"ok": True, "path": self.path})

            def do_POST(self) -> None:
                if self.path != "/slack/events":
                    _json_response(self, {"ok": False, "error": "not_found"}, status=404)
                    return

                raw_len = int(self.headers.get("Content-Length", "0") or "0")
                raw_body = self.rfile.read(raw_len) if raw_len > 0 else b""

                # Slack retry headers; avoid duplicate side effects.
                if self.headers.get("X-Slack-Retry-Num"):
                    _json_response(self, {"ok": True})
                    return

                if not server_self._verify_signature(self.headers, raw_body):
                    _json_response(self, {"ok": False, "error": "invalid_signature"}, status=401)
                    return

                try:
                    payload = json.loads(raw_body.decode("utf-8"))
                except Exception:
                    _json_response(self, {"ok": False, "error": "invalid_json"}, status=400)
                    return

                # URL verification handshake
                if payload.get("type") == "url_verification":
                    challenge = payload.get("challenge", "")
                    _json_response(self, {"challenge": challenge})
                    return

                if payload.get("type") != "event_callback":
                    _json_response(self, {"ok": True})
                    return

                event = payload.get("event") or {}
                # Ignore bot events to prevent loops
                if event.get("subtype") == "bot_message" or event.get("bot_id"):
                    _json_response(self, {"ok": True})
                    return

                if event.get("type") == "app_mention":
                    channel = str(event.get("channel") or "").strip()
                    text = str(event.get("text") or "").strip().lower()
                    server_self._handle_mention(channel=channel, text=text)
                    _json_response(self, {"ok": True})
                    return

                _json_response(self, {"ok": True})

        self._server = ThreadingHTTPServer((self._host, self._port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        self._log(f"Slack bot listening on http://{self._host}:{self._port}/slack/events")

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        try:
            server.shutdown()
        except Exception:
            pass
        try:
            server.server_close()
        except Exception:
            pass
        self._server = None
        self._thread = None
        self._log("Slack bot stopped.")

    def _verify_signature(self, headers, raw_body: bytes) -> bool:
        timestamp = str(headers.get("X-Slack-Request-Timestamp", "") or "")
        signature = str(headers.get("X-Slack-Signature", "") or "")
        if not timestamp or not signature:
            return False

        try:
            ts_i = int(timestamp)
        except Exception:
            return False

        # Reject replays (5 minutes)
        if abs(int(time.time()) - ts_i) > 60 * 5:
            return False

        base = b"v0:" + timestamp.encode("utf-8") + b":" + raw_body
        digest = hmac.new(self._secret, base, hashlib.sha256).hexdigest()
        expected = "v0=" + digest
        return hmac.compare_digest(expected, signature)

    def _handle_mention(self, *, channel: str, text: str) -> None:
        if not channel:
            return

        try:
            status = self._status_provider() or {}
        except Exception as exc:
            self._log(f"Slack bot status_provider failed: {exc}")
            status = {}

        state = status.get("state", "unknown")
        idx = status.get("current_index")
        total = status.get("total")
        label = status.get("current_label")
        session_name = status.get("session_name")
        experiment_name = status.get("experiment_name")

        msg = f"Queue status: {state}"
        if idx and total:
            msg += f" | step {idx}/{total}"
        if label:
            msg += f" | {label}"
        if session_name or experiment_name:
            msg += f"\nSession={session_name or '(none)'}; Experiment={experiment_name or '(none)'}"

        # Allow "help" mention
        if "help" in text:
            msg = (
                "Commands:\n"
                "- mention me with `status` to get the queue state\n"
                "- mention me with `help` to see this message"
            )

        if "status" in text or "help" in text or not text:
            self._notifier.send_message(msg, target=channel)

