"""Persistent library map for generated Opentrons protocols."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import OPENTRONS_LIBRARY_DIR, OPENTRONS_LIBRARY_MAP_FILE, OPENTRONS_PROTOCOLS_DIR
from core.opentrons_identity import resolve_protocol_id

_map: dict = {}


def _filename_stem_from_note(note: Optional[str], fallback: str) -> str:
    raw = str(note or "").strip() or fallback
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", raw)
    cleaned = re.sub(r"\s+", " ", cleaned).strip().rstrip(".")
    return cleaned or fallback


def _unique_library_path(stem: str) -> Path:
    base = Path(OPENTRONS_LIBRARY_DIR)
    candidate = base / f"{stem}.py"
    if not candidate.exists():
        return candidate
    index = 1
    while True:
        candidate = base / f"{stem} ({index}).py"
        if not candidate.exists():
            return candidate
        index += 1


def _ensure_dirs() -> None:
    Path(OPENTRONS_PROTOCOLS_DIR).mkdir(exist_ok=True)
    Path(OPENTRONS_LIBRARY_DIR).mkdir(exist_ok=True)


def load_map() -> dict:
    global _map
    _ensure_dirs()
    if _map:
        return _map
    path = Path(OPENTRONS_LIBRARY_MAP_FILE)
    if path.exists():
        try:
            _map = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            _map = {}
    return _map


def _persist() -> None:
    Path(OPENTRONS_LIBRARY_MAP_FILE).write_text(
        json.dumps(_map, indent=2),
        encoding="utf-8",
    )


def compute_hash(kind: str, params: dict) -> str:
    slug = (kind or "protocol").strip().lower().replace(" ", "_")
    canonical = json.dumps(
        {k: params[k] for k in sorted(params)},
        separators=(",", ":"),
        sort_keys=True,
    )
    try:
        digest = hashlib.md5(canonical.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    except TypeError:
        digest = hashlib.md5(canonical.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{digest}"


def lookup(hash_key: str) -> Optional[Path]:
    load_map()
    entry = _map.get(hash_key)
    if entry is None:
        return None
    path = Path(entry.get("filepath", ""))
    if path.exists():
        return path
    del _map[hash_key]
    _persist()
    return None


def register(
    hash_key: str,
    kind: str,
    params: dict,
    source: str,
    note: Optional[str] = None,
    protocol_id: Optional[str] = None,
) -> Path:
    _ensure_dirs()
    stem = _filename_stem_from_note(note, fallback=hash_key)
    path = _unique_library_path(stem)
    path.write_text(source, encoding="utf-8")
    resolved_protocol_id = resolve_protocol_id(
        protocol_id=protocol_id,
        protocol_name=(note or "").strip() or path.stem,
        filename=path.name,
    )
    _map[hash_key] = {
        "kind": kind,
        "params": params,
        "note": (note or "").strip(),
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "filepath": str(path),
        "protocol_id": resolved_protocol_id,
    }
    _persist()
    return path


def all_entries() -> dict:
    load_map()
    return dict(_map)


def entry_for_path(filepath) -> Optional[tuple[str, dict]]:
    load_map()
    target = Path(filepath).resolve()
    for key, entry in _map.items():
        try:
            entry_path = Path(entry.get("filepath", "")).resolve()
        except Exception:
            continue
        if entry_path != target:
            continue
        payload = dict(entry)
        payload["hash_key"] = key
        payload["protocol_id"] = resolve_protocol_id(
            protocol_id=payload.get("protocol_id"),
            protocol_name=str(payload.get("note") or entry_path.stem),
            filename=entry_path.name,
            library_key=key,
        )
        return key, payload
    return None


def update_protocol(
    hash_key: str,
    *,
    kind: str,
    params: dict,
    source: str,
    note: Optional[str] = None,
    protocol_id: Optional[str] = None,
) -> Optional[Path]:
    load_map()
    entry = _map.get(hash_key)
    if entry is None:
        return None
    path = Path(entry.get("filepath", ""))
    if not path.is_absolute():
        path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding="utf-8")
    resolved_note = (note or "").strip()
    resolved_protocol_id = resolve_protocol_id(
        protocol_id=protocol_id or entry.get("protocol_id"),
        protocol_name=resolved_note or str(entry.get("note") or path.stem),
        filename=path.name,
        library_key=hash_key,
    )
    entry.update(
        {
            "kind": kind,
            "params": params,
            "note": resolved_note,
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "filepath": str(path),
            "protocol_id": resolved_protocol_id,
        }
    )
    _persist()
    return path


def remove(hash_key: str) -> bool:
    load_map()
    entry = _map.get(hash_key)
    if entry is None:
        return False
    path = Path(entry.get("filepath", ""))
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
    del _map[hash_key]
    _persist()
    return True


def update_note(hash_key: str, note: Optional[str]) -> bool:
    load_map()
    entry = _map.get(hash_key)
    if not entry:
        return False
    new_note = (note or "").strip()
    if entry.get("note", "") == new_note:
        return False
    entry["note"] = new_note
    _persist()
    return True
