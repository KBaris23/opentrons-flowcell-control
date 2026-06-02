"""Best-effort ETA helpers for queued electrochemistry runs."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from core.pump_step_utils import estimate_eta_seconds
from methods import library_map


@dataclass
class ItemEstimate:
    seconds: Optional[float]
    item_type: str
    label: str
    unknown: bool = False
    alert_zero_counted: bool = False


@dataclass
class QueueEta:
    scope: str
    predicted_seconds: Optional[float]
    item_count: int
    estimated_items: int
    unknown_items: int
    alert_zero_counted: int
    step_delay_seconds: float = 0.0


@dataclass
class RunningQueueEta:
    current_remaining_seconds: Optional[float]
    remaining_after_current_seconds: Optional[float]
    total_remaining_seconds: Optional[float]
    current_step_unknown: bool
    waiting_for_alert: bool
    after_current: QueueEta


def _float(value, default: Optional[float] = None) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _si_number(token: str) -> Optional[float]:
    s = str(token or "").strip().lower()
    if not s:
        return None
    if s.endswith("i"):
        s = s[:-1]
    m = re.match(r"^([-+]?\d+(?:\.\d+)?)([munpk]?)", s)
    if not m:
        return None
    value = float(m.group(1))
    scale = {"m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "k": 1e3}.get(m.group(2), 1.0)
    return value * scale


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    total = max(0, int(round(float(seconds))))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def eta_finish_time(seconds: Optional[float]) -> str:
    if seconds is None:
        return "unknown"
    return (datetime.now() + timedelta(seconds=max(0.0, float(seconds)))).strftime("%Y-%m-%d %H:%M:%S")


def _registry_params_for_item(item: dict) -> tuple[str, dict]:
    script_path = Path(str(item.get("script_path") or ""))
    stem = script_path.stem
    try:
        entries = library_map.all_entries()
    except Exception:
        entries = {}
    entry = entries.get(stem)
    if entry is None:
        norm = str(script_path).replace("/", "\\").lower()
        for candidate in entries.values():
            if str(candidate.get("filepath") or "").replace("/", "\\").lower() == norm:
                entry = candidate
                break
    if not entry:
        return str(item.get("type") or "").upper(), {}
    return str(entry.get("technique") or item.get("type") or "").upper(), dict(entry.get("params") or {})


def _estimate_from_params(technique: str, params: dict) -> Optional[float]:
    tech = technique.upper()
    cond = _float(params.get("cond_time"), 0.0) or 0.0
    scans = max(1, int(_float(params.get("n_scans"), 1) or 1))
    if tech == "CV":
        begin = _float(params.get("begin_potential"))
        v1 = _float(params.get("vertex1"))
        v2 = _float(params.get("vertex2"))
        rate = _float(params.get("scan_rate"))
        if None in (begin, v1, v2, rate) or rate <= 0:
            return None
        span = abs(v1 - begin) + abs(v2 - v1) + abs(begin - v2)
        return cond + (span / rate) * scans + 3.0
    if tech == "LSV":
        begin = _float(params.get("begin_potential"))
        end = _float(params.get("end_potential"))
        rate = _float(params.get("scan_rate"))
        if None in (begin, end, rate) or rate <= 0:
            return None
        return cond + abs(end - begin) / rate + 3.0
    if tech == "SWV":
        begin = _float(params.get("begin_potential"))
        end = _float(params.get("end_potential"))
        step = abs(_float(params.get("step_potential"), 0.0) or 0.0)
        freq = _float(params.get("frequency"))
        if None in (begin, end, freq) or step <= 0 or freq <= 0:
            return None
        points = max(1, int(math.ceil(abs(end - begin) / step)) + 1)
        return cond + points / freq + 3.0
    if tech in {"EIS", "ALIGNMENT"}:
        start = _float(params.get("start_frequency"))
        end = _float(params.get("end_frequency"))
        ppd = _float(params.get("points_per_decade"), 10.0) or 10.0
        if start and end and start > 0 and end > 0:
            points = max(1, int(round(abs(math.log10(end) - math.log10(start)) * ppd)) + 1)
            return cond + points * 1.5 + 5.0
    return None


def _estimate_from_script(item: dict) -> Optional[float]:
    try:
        text = Path(str(item.get("script_path") or "")).read_text(encoding="utf-8")
    except Exception:
        return None
    wait_total = sum((_si_number(m.group(1)) or 0.0) for m in re.finditer(r"^\s*wait\s+(\S+)", text, re.MULTILINE))
    m = re.search(r"meas_loop_cv\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)(?:\s+nscans\((\d+)\))?", text)
    if m:
        begin, v1, v2, rate = (_si_number(m.group(i)) for i in range(1, 5))
        scans = int(m.group(5) or "1")
        if None not in (begin, v1, v2, rate) and rate and rate > 0:
            return wait_total + ((abs(v1 - begin) + abs(v2 - v1) + abs(begin - v2)) / rate) * scans + 3.0
    m = re.search(r"meas_loop_lsv\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)", text)
    if m:
        begin, end, rate = (_si_number(m.group(i)) for i in range(1, 4))
        if None not in (begin, end, rate) and rate and rate > 0:
            return wait_total + abs(end - begin) / rate + 3.0
    m = re.search(r"meas_loop_swv\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)\s+(\S+)\s+(\S+)\s+\S+\s+(\S+)", text)
    if m:
        begin, end, step, freq = (_si_number(m.group(i)) for i in range(1, 5))
        if None not in (begin, end, step, freq) and step and step > 0 and freq and freq > 0:
            points = max(1, int(math.ceil(abs(end - begin) / abs(step))) + 1)
            return wait_total + points / freq + 3.0
    m = re.search(r"meas_loop_eis\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+\S+\s+(\S+)", text)
    if m:
        points = _si_number(m.group(1))
        if points and points > 0:
            return wait_total + points * 1.5 + 5.0
    return wait_total if wait_total > 0 else None


def estimate_item_seconds(item: dict) -> Optional[float]:
    return estimate_item(item).seconds


def estimate_item(item: dict) -> ItemEstimate:
    item_type = str((item or {}).get("type") or "").strip().upper()
    label = str((item or {}).get("details") or item_type or "(unknown item)")
    if item_type == "ALERT":
        return ItemEstimate(0.0, item_type, label, alert_zero_counted=True)
    if item_type == "PAUSE":
        seconds = _float((item or {}).get("pause_seconds"))
        return ItemEstimate(seconds, item_type, label, unknown=seconds is None)
    if item_type.startswith("PUMP_"):
        action = str(((item or {}).get("pump_action") or {}).get("name") or item_type.replace("PUMP_", "")).upper()
        params = dict(((item or {}).get("pump_action") or {}).get("params") or {})
        if action in {"APPLY", "STATUS", "STATUS_PORT", "STOP", "RESTART", "PAUSE", "STATE_RESET"}:
            return ItemEstimate(1.0, item_type, label)
        if action in {"HEXW2", "START"}:
            seconds = estimate_eta_seconds(params.get("volume"), params.get("rate"), str(params.get("units") or ""))
            delay = _float(params.get("delay_min"), 0.0) or 0.0
            if seconds is not None:
                return ItemEstimate(seconds + delay * 60.0 + 2.0, item_type, label)
        return ItemEstimate(None, item_type, label, unknown=True)
    if item_type.startswith("OPENTRONS_") or item_type.startswith("MISC_"):
        return ItemEstimate(None, item_type, label, unknown=True)
    technique, params = _registry_params_for_item(item or {})
    seconds = _estimate_from_params(technique or item_type, params) if params else None
    if seconds is None:
        seconds = _estimate_from_script(item or {})
    return ItemEstimate(seconds, item_type, label, unknown=seconds is None)


def estimate_queue_eta(queue: list[dict], start_index: int = 0, step_delay_seconds: float = 0.0, scope: str = "entire queue") -> QueueEta:
    items = list(queue or [])[max(0, int(start_index or 0)):]
    total = 0.0
    estimated = 0
    unknown = 0
    alerts = 0
    for idx, item in enumerate(items):
        est = estimate_item(item)
        if est.alert_zero_counted:
            alerts += 1
        if est.seconds is None:
            unknown += 1
        else:
            total += max(0.0, float(est.seconds))
            estimated += 1
        if idx < len(items) - 1:
            total += max(0.0, float(step_delay_seconds or 0.0))
    return QueueEta(scope, total, len(items), estimated, unknown, alerts, max(0.0, float(step_delay_seconds or 0.0)))


def estimate_running_queue_eta(
    queue: list[dict],
    next_index: int,
    current_step_elapsed_seconds: float,
    current_step_estimated_seconds: Optional[float],
    step_delay_seconds: float,
    include_next_step_delay: bool = True,
    current_step_type: str = "",
) -> RunningQueueEta:
    after = estimate_queue_eta(queue, start_index=next_index, step_delay_seconds=step_delay_seconds, scope="active run")
    current_type = str(current_step_type or "").upper()
    waiting_alert = current_type == "ALERT"
    current_unknown = current_step_estimated_seconds is None and not waiting_alert
    current_remaining = None
    if waiting_alert:
        current_remaining = None
    elif current_step_estimated_seconds is not None:
        current_remaining = max(0.0, float(current_step_estimated_seconds) - max(0.0, float(current_step_elapsed_seconds or 0.0)))
    after_seconds = after.predicted_seconds
    if include_next_step_delay and next_index < len(queue):
        after_seconds = (after_seconds or 0.0) + max(0.0, float(step_delay_seconds or 0.0))
    total = None if current_unknown or waiting_alert else (current_remaining or 0.0) + (after_seconds or 0.0)
    return RunningQueueEta(current_remaining, after_seconds, total, current_unknown, waiting_alert, after)


def format_static_eta_text(eta: QueueEta) -> str:
    lines = [
        f"Estimate scope: {eta.scope}",
        f"Predicted duration: {format_duration(eta.predicted_seconds)}",
        f"Estimated finish: {eta_finish_time(eta.predicted_seconds)}",
    ]
    if eta.unknown_items:
        lines.append(f"Unknown items not counted: {eta.unknown_items}")
    if eta.alert_zero_counted:
        lines.append(f"Alert pauses treated as 0 sec: {eta.alert_zero_counted}")
    if not eta.unknown_items and not eta.alert_zero_counted:
        lines.append("All queued items were estimated.")
    return "\n".join(lines)


def format_live_eta_text(eta: RunningQueueEta, *, current_index: int, total: int, current_label: str) -> str:
    if eta.waiting_for_alert:
        current_remaining = "waiting for alert acknowledgment"
    elif eta.current_step_unknown:
        current_remaining = "unknown until current step finishes"
    else:
        current_remaining = format_duration(eta.current_remaining_seconds)
    total_remaining = "unknown until current step finishes" if eta.total_remaining_seconds is None else format_duration(eta.total_remaining_seconds)
    return "\n".join([
        "Estimate scope: active run",
        f"Current step: {current_index}/{total} | {current_label or '(unknown step)'}",
        f"Current step remaining: {current_remaining}",
        f"Remaining after this step: {format_duration(eta.remaining_after_current_seconds)}",
        f"Total remaining: {total_remaining}",
        f"Estimated finish: {eta_finish_time(eta.total_remaining_seconds)}",
    ])
