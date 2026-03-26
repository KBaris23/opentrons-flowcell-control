"""Persistent syringe state tracking for collection workflows."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from config import DATA_DIR, COLLECTION_SYRINGE_CAPACITY_ML
from .pump_step_utils import default_collection_warn_ml


class SyringeStateRegistry:
    """Stores the current syringe collection state on disk.

    The registry is intentionally human-readable so operators can inspect or
    edit it if needed between GUI sessions.
    """

    def __init__(
        self,
        path: Optional[Path] = None,
        log_callback: Optional[Callable[[str], None]] = None,
    ):
        self.path = Path(path) if path else Path(DATA_DIR) / "syringe_state_registry.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._log = log_callback or (lambda _msg: None)
        self._payload = self._load()

    @staticmethod
    def _timestamp() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _default_current_state() -> dict:
        capacity_ul = float(COLLECTION_SYRINGE_CAPACITY_ML) * 1000.0
        warn_ul = float(default_collection_warn_ml(COLLECTION_SYRINGE_CAPACITY_ML)) * 1000.0
        now = SyringeStateRegistry._timestamp()
        return {
            "steps": 0,
            "volume_ul": 0.0,
            "capacity_ul": capacity_ul,
            "warn_ul": warn_ul,
            "warned": False,
            "last_event": "initialized",
            "last_reset_at": now,
            "updated_at": now,
        }

    def _default_payload(self) -> dict:
        return {
            "version": 1,
            "updated_at": self._timestamp(),
            "current_state": self._default_current_state(),
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
            self._log(f"[SyringeRegistry] Could not read registry, recreating defaults: {exc}")
            payload = self._default_payload()
            self._write(payload)
            return payload

        if not isinstance(payload, dict):
            payload = self._default_payload()
        payload.setdefault("version", 1)
        payload.setdefault("updated_at", self._timestamp())
        payload["current_state"] = self._normalized_state(payload.get("current_state"))
        history = payload.get("history")
        payload["history"] = history if isinstance(history, list) else []
        return payload

    def _normalized_state(self, raw_state) -> dict:
        state = self._default_current_state()
        if isinstance(raw_state, dict):
            for key in ("steps", "volume_ul", "capacity_ul", "warn_ul", "warned", "last_event", "last_reset_at", "updated_at"):
                if key in raw_state:
                    state[key] = raw_state[key]

        try:
            state["steps"] = max(0, int(state.get("steps", 0)))
        except Exception:
            state["steps"] = 0
        for key, fallback in (
            ("volume_ul", 0.0),
            ("capacity_ul", float(COLLECTION_SYRINGE_CAPACITY_ML) * 1000.0),
        ):
            try:
                state[key] = max(0.0, float(state.get(key, fallback)))
            except Exception:
                state[key] = fallback
        try:
            warn_ul = float(state.get("warn_ul", default_collection_warn_ml(state["capacity_ul"] / 1000.0) * 1000.0))
        except Exception:
            warn_ul = default_collection_warn_ml(state["capacity_ul"] / 1000.0) * 1000.0
        state["warn_ul"] = max(0.0, warn_ul)
        state["warned"] = bool(state.get("warned", False))
        state["last_event"] = str(state.get("last_event") or "initialized")
        state["last_reset_at"] = str(state.get("last_reset_at") or self._timestamp())
        state["updated_at"] = str(state.get("updated_at") or self._timestamp())
        return state

    def _write(self, payload: dict) -> None:
        payload["updated_at"] = self._timestamp()
        self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def snapshot(self) -> dict:
        return dict(self._payload.get("current_state") or {})

    def _append_history(self, entry: dict) -> None:
        history = list(self._payload.get("history") or [])
        history.append(entry)
        self._payload["history"] = history[-200:]

    def _save_current_state(self, state: dict, event: dict) -> dict:
        normalized = self._normalized_state(state)
        normalized["updated_at"] = self._timestamp()
        self._payload["current_state"] = normalized
        self._append_history(event)
        self._write(self._payload)
        return dict(normalized)

    def record_collection(
        self,
        *,
        volume_ul: float,
        steps: int,
        total_volume_ul: float,
        capacity_ul: float,
        warn_ul: float,
        warned: bool,
        context: Optional[dict] = None,
    ) -> dict:
        state = self.snapshot()
        state.update(
            {
                "steps": max(0, int(steps)),
                "volume_ul": max(0.0, float(total_volume_ul)),
                "capacity_ul": max(0.0, float(capacity_ul)),
                "warn_ul": max(0.0, float(warn_ul)),
                "warned": bool(warned),
                "last_event": "collection_added",
            }
        )
        event = {
            "timestamp": self._timestamp(),
            "event": "collection_added",
            "delta_volume_ul": max(0.0, float(volume_ul)),
            "current_state": {
                "steps": state["steps"],
                "volume_ul": state["volume_ul"],
                "capacity_ul": state["capacity_ul"],
                "warn_ul": state["warn_ul"],
                "warned": state["warned"],
            },
            "context": dict(context or {}),
        }
        return self._save_current_state(state, event)

    def reset(
        self,
        *,
        capacity_ul: Optional[float] = None,
        warn_ul: Optional[float] = None,
        reason: str = "manual reset",
        context: Optional[dict] = None,
    ) -> dict:
        state = self._default_current_state()
        if capacity_ul is not None:
            state["capacity_ul"] = max(0.0, float(capacity_ul))
        if warn_ul is not None:
            state["warn_ul"] = max(0.0, float(warn_ul))
        else:
            state["warn_ul"] = max(0.0, default_collection_warn_ml(state["capacity_ul"] / 1000.0) * 1000.0)
        now = self._timestamp()
        state["last_event"] = reason
        state["last_reset_at"] = now
        state["updated_at"] = now
        event = {
            "timestamp": now,
            "event": "reset",
            "reason": str(reason or "manual reset"),
            "current_state": {
                "steps": state["steps"],
                "volume_ul": state["volume_ul"],
                "capacity_ul": state["capacity_ul"],
                "warn_ul": state["warn_ul"],
                "warned": state["warned"],
            },
            "context": dict(context or {}),
        }
        return self._save_current_state(state, event)
