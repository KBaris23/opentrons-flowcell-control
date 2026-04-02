"""Persistent storage for generated Opentrons protocols."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from config import OPENTRONS_LIBRARY_DIR
from robot.opentrons_library_map import (
    all_entries,
    compute_hash,
    entry_for_path,
    lookup,
    register,
    remove,
    update_note,
    update_protocol as update_library_protocol,
)


class OpentronsRegistry:
    """Manages saving and deduplication of generated Opentrons protocols."""

    def __init__(
        self,
        log_callback: Callable[[str], None] = print,
        base_path: Optional[Path] = None,
    ):
        self._log = log_callback
        self.base_path = Path(base_path) if base_path else Path(OPENTRONS_LIBRARY_DIR)
        self.base_path.mkdir(parents=True, exist_ok=True)
        self._registry: Dict[str, Tuple[Path, str]] = {}
        self._path_to_key: Dict[str, str] = {}

    def save_protocol(
        self,
        kind: str,
        source: str,
        params: Optional[dict] = None,
        note: Optional[str] = None,
    ) -> Tuple[Path, str, bool]:
        if params is None:
            params = {"_source_hash": hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]}
        key = compute_hash(kind, params)

        if key in self._registry:
            path, filename = self._registry[key]
            if note is not None:
                update_note(key, note)
            self._log(f"[Opentrons Library] Session hit '{filename}' ({key})")
            return path, filename, False

        lib_path = lookup(key)
        if lib_path is not None:
            filename = lib_path.name
            self._registry[key] = (lib_path, filename)
            self._path_to_key[str(lib_path)] = key
            if note is not None:
                update_note(key, note)
            self._log(f"[Opentrons Library] Found '{filename}' ({key})")
            return lib_path, filename, False

        lib_path = register(key, kind, params, source, note=note)
        filename = lib_path.name
        self._registry[key] = (lib_path, filename)
        self._path_to_key[str(lib_path)] = key
        self._log(f"[Opentrons Library] Saved new '{filename}' ({key})")
        return lib_path, filename, True

    def hash_key_for(self, filepath) -> str:
        return self._path_to_key.get(str(filepath), "-")

    def clear(self) -> None:
        count = len(self._registry)
        self._registry.clear()
        self._path_to_key.clear()
        self._log(f"[Opentrons Library] Cleared ({count} entries).")

    def all_entries(self) -> Dict[str, dict]:
        return all_entries()

    def entry_for_path(self, filepath) -> Optional[tuple[str, dict]]:
        return entry_for_path(filepath)

    def update_protocol(
        self,
        filepath,
        *,
        kind: str,
        source: str,
        params: Optional[dict] = None,
        note: Optional[str] = None,
    ) -> tuple[Path, str]:
        found = self.entry_for_path(filepath)
        if found is None:
            raise FileNotFoundError(f"Selected file is not a saved library protocol: {filepath}")
        key, entry = found
        if params is None:
            params = {"_source_hash": hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]}
        protocol_id = str(entry.get("protocol_id") or "").strip() or None
        lib_path = update_library_protocol(
            key,
            kind=kind,
            params=params,
            source=source,
            note=note,
            protocol_id=protocol_id,
        )
        if lib_path is None:
            raise FileNotFoundError(f"Library entry disappeared while updating: {filepath}")
        filename = lib_path.name
        self._registry[key] = (lib_path, filename)
        self._path_to_key[str(lib_path)] = key
        self._log(f"[Opentrons Library] Updated '{filename}' ({key})")
        return lib_path, filename

    def delete_protocol(self, filepath) -> bool:
        found = self.entry_for_path(filepath)
        if found is None:
            return False
        key, entry = found
        removed = remove(key)
        try:
            path = Path(entry.get("filepath", ""))
            self._registry.pop(key, None)
            self._path_to_key.pop(str(path), None)
        except Exception:
            pass
        if removed:
            self._log(f"[Opentrons Library] Deleted '{Path(entry.get('filepath', '')).name}' ({key})")
        return removed

    @property
    def size(self) -> int:
        return len(self._registry)
