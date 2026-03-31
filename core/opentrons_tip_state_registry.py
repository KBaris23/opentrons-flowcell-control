"""Persistent tip tracking for builder-generated Opentrons protocols."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import OPENTRONS_TIP_STATE_FILE


class OpentronsTipStateRegistry:
    """Stores the next suggested starting tip for known tiprack setups."""

    def __init__(
        self,
        path: Optional[Path] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.path = Path(path) if path else Path(OPENTRONS_TIP_STATE_FILE)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_callback or (lambda _msg: None)
        self._payload = self._load()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().isoformat(timespec="seconds")

    def _default_payload(self) -> dict:
        return {
            "version": 1,
            "updated_at": self._timestamp(),
            "tipracks": {},
            "history": [],
        }

    def _load(self) -> dict:
        if not self.path.exists():
            payload = self._default_payload()
            self._write(payload)
            return payload
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception as exc:
            self._log(f"[OpentronsTipTracker] Could not read registry, recreating defaults: {exc}")
            payload = self._default_payload()
            self._write(payload)
            return payload
        if not isinstance(payload, dict):
            payload = self._default_payload()
        payload.setdefault("version", 1)
        payload.setdefault("updated_at", self._timestamp())
        payload["tipracks"] = payload.get("tipracks") if isinstance(payload.get("tipracks"), dict) else {}
        payload["history"] = payload.get("history") if isinstance(payload.get("history"), list) else []
        return payload

    def _write(self, payload: dict) -> None:
        payload["updated_at"] = self._timestamp()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def snapshot(self, tracker_key: str) -> dict:
        entry = dict((self._payload.get("tipracks") or {}).get(tracker_key) or {})
        return {
            "next_tip": str(entry.get("next_tip") or "A1"),
            "tips_used_total": max(0, int(entry.get("tips_used_total", 0) or 0)),
            "last_starting_tip": str(entry.get("last_starting_tip") or ""),
            "last_protocol_name": str(entry.get("last_protocol_name") or ""),
            "last_event": str(entry.get("last_event") or "initialized"),
            "updated_at": str(entry.get("updated_at") or ""),
        }

    def _append_history(self, event: dict) -> None:
        history = list(self._payload.get("history") or [])
        history.append(event)
        self._payload["history"] = history[-300:]

    def record_protocol(
        self,
        *,
        tracker_key: str,
        protocol_name: str,
        starting_tip: str,
        tips_used: int,
        next_tip: str | None,
        context: Optional[dict] = None,
        event_name: str = "protocol_saved",
    ) -> dict:
        current = self.snapshot(tracker_key)
        updated = {
            "next_tip": str(next_tip or ""),
            "tips_used_total": current["tips_used_total"] + max(0, int(tips_used)),
            "last_starting_tip": str(starting_tip or ""),
            "last_protocol_name": str(protocol_name or ""),
            "last_event": str(event_name or "protocol_saved"),
            "updated_at": self._timestamp(),
        }
        tipracks = dict(self._payload.get("tipracks") or {})
        tipracks[tracker_key] = updated
        self._payload["tipracks"] = tipracks
        self._append_history(
            {
                "timestamp": self._timestamp(),
                "event": str(event_name or "protocol_saved"),
                "tracker_key": tracker_key,
                "protocol_name": str(protocol_name or ""),
                "starting_tip": str(starting_tip or ""),
                "tips_used": max(0, int(tips_used)),
                "next_tip": str(next_tip or ""),
                "context": dict(context or {}),
            }
        )
        self._write(self._payload)
        return dict(updated)

    def reset_tiprack(
        self,
        *,
        tracker_key: str,
        next_tip: str = "A1",
        context: Optional[dict] = None,
        reason: str = "manual reset",
    ) -> dict:
        updated = {
            "next_tip": str(next_tip or "A1"),
            "tips_used_total": 0,
            "last_starting_tip": "",
            "last_protocol_name": "",
            "last_event": str(reason or "manual reset"),
            "updated_at": self._timestamp(),
        }
        tipracks = dict(self._payload.get("tipracks") or {})
        tipracks[tracker_key] = updated
        self._payload["tipracks"] = tipracks
        self._append_history(
            {
                "timestamp": self._timestamp(),
                "event": "reset",
                "tracker_key": tracker_key,
                "next_tip": str(next_tip or "A1"),
                "reason": str(reason or "manual reset"),
                "context": dict(context or {}),
            }
        )
        self._write(self._payload)
        return dict(updated)
