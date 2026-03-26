"""Shared helpers for pump-step ETA, volume conversion, and titration details."""

from __future__ import annotations

from typing import Optional

from config import COLLECTION_SYRINGE_CAPACITY_ML, COLLECTION_SYRINGE_WARN_FRACTION


def normalize_units(units: str) -> str:
    return str(units or "").strip().lower().replace("/", "").replace("_", "")


def volume_to_ul(volume: float, units: str) -> Optional[float]:
    try:
        value = float(volume)
    except (TypeError, ValueError):
        return None
    unit_key = normalize_units(units)
    if unit_key.startswith("ml"):
        return value * 1000.0
    if unit_key.startswith("ul"):
        return value
    return None


def estimate_eta_seconds(volume: float, rate: float, units: str) -> Optional[float]:
    try:
        vol = float(volume)
        rate_value = float(rate)
    except (TypeError, ValueError):
        return None
    if rate_value <= 0:
        return None

    unit_key = normalize_units(units)
    if unit_key.endswith("hr"):
        return (vol / rate_value) * 3600.0
    return (vol / rate_value) * 60.0


def rate_for_target_eta(volume: float, units: str, target_seconds: float) -> Optional[float]:
    try:
        vol = float(volume)
        target_s = float(target_seconds)
    except (TypeError, ValueError):
        return None
    if target_s <= 0:
        return None

    unit_key = normalize_units(units)
    if unit_key.endswith("hr"):
        return vol * 3600.0 / target_s
    return vol * 60.0 / target_s


def format_ml_from_ul(volume_ul: float) -> str:
    return f"{float(volume_ul) / 1000.0:.3f} mL"


def format_eta(seconds: float | None) -> str:
    if seconds is None:
        return ""
    return f"{float(seconds):.1f}s"


def default_collection_warn_ml(capacity_ml: float | None) -> float:
    try:
        capacity = float(capacity_ml)
    except (TypeError, ValueError):
        return 0.0
    if capacity <= 0:
        return 0.0
    return capacity * float(COLLECTION_SYRINGE_WARN_FRACTION)


def build_pump_details(name: str, params: dict) -> str:
    action = str(name or "").strip().upper()
    params = dict(params or {})

    if action == "COMMAND":
        cmd = str(params.get("cmd") or "").strip()
        return f"Pump cmd: {cmd}" if cmd else "Pump cmd"

    if action == "APPLY":
        units = str(params.get("units") or "")
        mode = str(params.get("mode") or "")
        diam = params.get("diameter_mm")
        try:
            diam_s = f"{float(diam):g}mm" if diam is not None else "?mm"
        except (TypeError, ValueError):
            diam_s = "?mm"
        return f"Pump: Apply ({units}, {mode}, O{diam_s})"

    if action == "HEXW2":
        units = str(params.get("units") or "")
        mode = str(params.get("mode") or "")
        try:
            volume = float(params.get("volume"))
            rate = float(params.get("rate"))
        except (TypeError, ValueError):
            return f"Pump: HEXW2 ({mode})"

        parts = [f"Pump: HEXW2 {mode} {volume:g} @ {rate:g} ({units})"]
        eta_s = estimate_eta_seconds(volume, rate, units)
        if eta_s is not None:
            parts.append(f"eta {format_eta(eta_s)}")

        if bool(params.get("track_collection")):
            volume_ul = volume_to_ul(volume, units)
            if volume_ul is not None:
                parts.append(f"collect {format_ml_from_ul(volume_ul)}")
            try:
                cap_ml = float(params.get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML))
                warn_ml = float(params.get("collection_warn_ml", default_collection_warn_ml(cap_ml)))
                parts.append(f"warn {warn_ml:g}/{cap_ml:g} mL")
            except (TypeError, ValueError):
                pass

        return " | ".join(parts)

    if action == "STATE_RESET":
        return "Syringe state reset"

    if action in {"START", "PAUSE", "STOP", "RESTART", "STATUS", "STATUS_PORT"}:
        return f"Pump: {action.replace('_', ' ').title()}"

    return f"Pump action {action}"
