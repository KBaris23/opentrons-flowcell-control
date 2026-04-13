# methods/library_map.py
"""
Persistent library of MethodSCRIPT files.

The library_map is a JSON file that survives across sessions. It maps a
parameter-based hash to a canonical .ms file stored in methods/library/.

The hash is computed from parameters (technique + raw param values +
mux channel), NOT from generated script text, so the same experimental
setup always maps to the same hash.
"""

import json
import hashlib
from pathlib import Path
from datetime import datetime
from typing import Optional

from core.methodscript_compat import (
    normalize_method_params,
    normalize_script_text,
    score_param_richness,
)

_METHODS_ROOT = Path("methods")
_LIBRARY_DIR = _METHODS_ROOT / "library"
_MUX_LIBRARY_DIR = _LIBRARY_DIR / "mux_methods"
_ARCHIVE_DIR = _METHODS_ROOT / "archive"
_MAP_FILE = _METHODS_ROOT / "library_map.json"

_map: dict = {}
_dedupe_done = False


def _ensure_dirs():
    _METHODS_ROOT.mkdir(exist_ok=True)
    _LIBRARY_DIR.mkdir(exist_ok=True)
    _MUX_LIBRARY_DIR.mkdir(exist_ok=True)
    _ARCHIVE_DIR.mkdir(exist_ok=True)


def _normalize_mux_channel(mux_channel) -> Optional[int]:
    if mux_channel in (None, "", 0, "0"):
        return None
    try:
        return int(mux_channel)
    except (TypeError, ValueError):
        return None


def _library_dir_for(mux_channel) -> Path:
    mux_channel = _normalize_mux_channel(mux_channel)
    if mux_channel is not None:
        return _MUX_LIBRARY_DIR
    return _LIBRARY_DIR


def load_map() -> dict:
    """Load library_map.json into memory. No-op if already loaded."""
    global _map
    _ensure_dirs()
    if _map:
        return _map
    if _MAP_FILE.exists():
        try:
            _map = json.loads(_MAP_FILE.read_text(encoding="utf-8"))
        except Exception:
            _map = {}
    _dedupe_exact_script_duplicates()
    return _map


def _persist():
    _MAP_FILE.write_text(json.dumps(_map, indent=2), encoding="utf-8")


def compute_hash(technique: str, params: dict, mux_channel: Optional[int]) -> str:
    """Compute a stable hash from parameters, not script text."""
    slug = technique.lower().replace(" ", "_")
    mux_channel = _normalize_mux_channel(mux_channel)
    if mux_channel is not None:
        slug = f"{slug}_ch{mux_channel}"

    normalized_params = normalize_method_params(params)
    canonical = json.dumps({k: normalized_params[k] for k in sorted(normalized_params)}, separators=(",", ":"))
    raw = f"{slug}||{canonical}"
    try:
        h = hashlib.md5(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:6]
    except TypeError:
        h = hashlib.md5(raw.encode("utf-8")).hexdigest()[:6]
    return f"{slug}_{h}"


def lookup(hash_key: str) -> Optional[Path]:
    """Return the library path if this hash exists and file is on disk."""
    load_map()
    entry = _map.get(hash_key)
    if entry is None:
        return None
    path = Path(entry["filepath"])
    if path.exists():
        return path
    # If a mux method was relocated into the mux_methods folder, fix the map.
    mux_channel = entry.get("mux_channel")
    alt_path = _library_dir_for(mux_channel) / f"{hash_key}.ms"
    if alt_path.exists():
        entry["filepath"] = str(alt_path)
        _persist()
        return alt_path
    del _map[hash_key]
    _persist()
    return None


def register(
    hash_key: str,
    technique: str,
    params: dict,
    mux_channel: Optional[int],
    script: str,
    note: Optional[str] = None,
) -> Path:
    """Write script into the library and record in the map."""
    _ensure_dirs()
    mux_channel = _normalize_mux_channel(mux_channel)
    lib_path = _library_dir_for(mux_channel) / f"{hash_key}.ms"
    lib_path.write_text(script, encoding="utf-8")
    normalized_params = normalize_method_params(params)

    _map[hash_key] = {
        "technique": technique,
        "mux_channel": mux_channel if mux_channel is not None else 0,
        "params": normalized_params,
        "note": (note or "").strip(),
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "filepath": str(lib_path),
    }
    _persist()
    return lib_path


def all_entries() -> dict:
    """Return full map."""
    load_map()
    return dict(_map)


def update_note(hash_key: str, note: Optional[str]) -> bool:
    """Update note for an existing entry. Returns True if changed."""
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


def find_by_technique(technique: str) -> dict:
    """Return all entries for a given technique."""
    load_map()
    t = technique.upper()
    return {k: v for k, v in _map.items() if v.get("technique", "").upper() == t}


def reload():
    """Force a full reload from disk."""
    global _map
    _map = {}
    load_map()


def _dedupe_exact_script_duplicates():
    global _dedupe_done
    if _dedupe_done:
        return
    _dedupe_done = True

    groups = {}
    for key, entry in list(_map.items()):
        path_text = entry.get("filepath")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.exists():
            continue
        normalized = normalize_script_text(path.read_text(encoding="utf-8"))
        groups.setdefault(normalized, []).append((key, entry, path))

    changed = False
    for members in groups.values():
        if len(members) < 2:
            continue
        members.sort(
            key=lambda item: (
                score_param_richness(item[1].get("params"))[0],
                score_param_richness(item[1].get("params"))[1],
                len(str(item[1].get("note", "")).strip()),
            ),
            reverse=True,
        )
        keep_key, keep_entry, keep_path = members[0]
        keep_entry["filepath"] = str(keep_path)
        for drop_key, _, drop_path in members[1:]:
            if drop_key in _map:
                del _map[drop_key]
                changed = True
            if drop_path != keep_path and drop_path.exists():
                try:
                    drop_path.unlink()
                    changed = True
                except OSError:
                    pass

    if changed:
        _persist()
