"""Persistent storage for generated Opentrons protocols."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

from config import OPENTRONS_LIBRARY_DIR
from robot.opentrons_library_map import compute_hash, lookup, register, update_note


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
    ) -> Tuple[Path, str]:
        if params is None:
            params = {"_source_hash": hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]}
        key = compute_hash(kind, params)

        if key in self._registry:
            path, filename = self._registry[key]
            if note is not None:
                update_note(key, note)
            self._log(f"[Opentrons Library] Session hit '{filename}' ({key})")
            return path, filename

        lib_path = lookup(key)
        if lib_path is not None:
            filename = lib_path.name
            self._registry[key] = (lib_path, filename)
            self._path_to_key[str(lib_path)] = key
            if note is not None:
                update_note(key, note)
            self._log(f"[Opentrons Library] Found '{filename}' ({key})")
            return lib_path, filename

        lib_path = register(key, kind, params, source, note=note)
        filename = lib_path.name
        self._registry[key] = (lib_path, filename)
        self._path_to_key[str(lib_path)] = key
        self._log(f"[Opentrons Library] Saved new '{filename}' ({key})")
        return lib_path, filename

    def hash_key_for(self, filepath) -> str:
        return self._path_to_key.get(str(filepath), "-")

    def clear(self) -> None:
        count = len(self._registry)
        self._registry.clear()
        self._path_to_key.clear()
        self._log(f"[Opentrons Library] Cleared ({count} entries).")

    @property
    def size(self) -> int:
        return len(self._registry)
