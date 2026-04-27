import hashlib
from pathlib import Path
from typing import Optional


VALID_BA_TOKENS = {
    "1n",
    "59n",
    "100n",
    "489n",
    "590n",
    "2u",
    "4u",
    "8u",
    "10u",
    "16u",
    "32u",
    "59u",
    "63u",
    "100u",
    "118u",
    "125u",
    "250u",
    "500u",
    "1m",
    "5m",
    "1180n",
    "2360n",
    "4720n",
    "7375n",
    "9440n",
    "14750n",
    "18880n",
    "29500n",
    "37170n",
    "73750n",
    "147500n",
    "295u",
    "590u",
    "2950u",
    "3687500p",
    "59m",
}


def normalize_script_text(script: str) -> str:
    text = str(script or "").replace("\r\n", "\n").replace("\r", "\n")
    return "\n".join(line.rstrip() for line in text.split("\n")).rstrip("\n")


def normalized_script_hash(script: str, length: int = 12) -> str:
    normalized = normalize_script_text(script)
    return hashlib.sha1(normalized.encode("utf-8")).hexdigest()[:length]


def normalize_method_params(params: Optional[dict]) -> dict:
    return {str(k): str(v).strip() for k, v in (params or {}).items()}


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
