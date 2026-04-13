import hashlib
import re
from pathlib import Path
from typing import Optional


SUPPORTED_BA_LABELS = ("59n", "489n", "10u", "100u", "1m", "5m")
VALID_BA_TOKENS = set(SUPPORTED_BA_LABELS) | {"2u", "59m"}

_CURRENT_FACTORS = {
    "a": 1e-18,
    "f": 1e-15,
    "p": 1e-12,
    "n": 1e-9,
    "u": 1e-6,
    "m": 1e-3,
    "": 1.0,
}


def normalize_script_text(script: str) -> str:
    text = str(script or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")


def normalized_script_hash(script: str, length: int = 12) -> str:
    normalized = normalize_script_text(script)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:length]


def _parse_current_value(label: object) -> Optional[float]:
    text = str(label or "").strip().replace(" ", "")
    if not text:
        return None
    text = text.replace("A", "")
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([afpnum]?)", text)
    if not match:
        return None
    value = float(match.group(1))
    suffix = match.group(2)
    return value * _CURRENT_FACTORS[suffix]


def normalize_current_range_label(label: object, role: str = "fixed") -> str:
    text = str(label or "").strip().replace(" ", "")
    if text in SUPPORTED_BA_LABELS:
        return text

    requested = _parse_current_value(text)
    if requested is None:
        raise ValueError(f"Unsupported BA/current-range value: {label}")

    supported = [(item, _parse_current_value(item)) for item in SUPPORTED_BA_LABELS]
    if role == "autorange_min":
        choices = [item for item in supported if item[1] <= requested]
        return (choices[-1] if choices else supported[0])[0]
    if role == "autorange_max":
        choices = [item for item in supported if item[1] >= requested]
        return (choices[0] if choices else supported[-1])[0]
    return min(supported, key=lambda item: abs(item[1] - requested))[0]


def normalize_method_params(params: Optional[dict]) -> dict:
    cleaned = {str(k): str(v).strip() for k, v in (params or {}).items()}
    role_by_key = {
        "current_range_fixed": "fixed",
        "current_range_autorange_min": "autorange_min",
        "current_range_autorange_max": "autorange_max",
    }
    for key, role in role_by_key.items():
        if key in cleaned and cleaned[key]:
            cleaned[key] = normalize_current_range_label(cleaned[key], role=role)
    return cleaned


def score_param_richness(params: Optional[dict]) -> tuple[int, int]:
    data = params or {}
    meaningful = 0
    fallback = 0
    for key, value in data.items():
        if str(value or "").strip() == "":
            continue
        if str(key).startswith("_") or str(key).endswith("_hash") or str(key) == "custom_hash":
            fallback += 1
        else:
            meaningful += 1
    return meaningful, -fallback


def validate_ba_tokens_in_script(script: str, script_path: Optional[Path] = None) -> None:
    text = normalize_script_text(script)
    lines = text.split("\n")
    has_measurement_loop = any(
        line.strip().startswith(("meas_loop_cv", "meas_loop_lsv", "meas_loop_swv"))
        for line in lines
    )
    has_set_range = False
    has_autorange = False

    for lineno, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("set_range ba "):
            has_set_range = True
            token = line.split()[2]
            if token not in VALID_BA_TOKENS:
                location = f"{script_path}:{lineno}" if script_path else f"line {lineno}"
                raise ValueError(f"Unsupported BA token '{token}' in {location}")
        if line.startswith("set_autoranging ba "):
            has_autorange = True
            tokens = line.split()
            for token in tokens[2:4]:
                if token not in VALID_BA_TOKENS:
                    location = f"{script_path}:{lineno}" if script_path else f"line {lineno}"
                    raise ValueError(f"Unsupported BA token '{token}' in {location}")

    if has_measurement_loop and (not has_set_range or not has_autorange):
        where = f" in {script_path}" if script_path else ""
        raise ValueError(f"MethodSCRIPT measurement scripts must emit explicit BA setup{where}")
