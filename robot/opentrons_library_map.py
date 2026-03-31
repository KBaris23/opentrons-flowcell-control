"""Persistent library map for generated Opentrons protocols."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from config import OPENTRONS_LIBRARY_DIR, OPENTRONS_LIBRARY_MAP_FILE, OPENTRONS_PROTOCOLS_DIR

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


def register(hash_key: str, kind: str, params: dict, source: str, note: Optional[str] = None) -> Path:
    _ensure_dirs()
    stem = _filename_stem_from_note(note, fallback=hash_key)
    path = _unique_library_path(stem)
    path.write_text(source, encoding="utf-8")
    _map[hash_key] = {
        "kind": kind,
        "params": params,
        "note": (note or "").strip(),
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "filepath": str(path),
    }
    _persist()
    return path


def all_entries() -> dict:
    load_map()
    return dict(_map)


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
