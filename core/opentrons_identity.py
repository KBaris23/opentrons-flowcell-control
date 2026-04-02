"""Stable Opentrons protocol identity and resume-key helpers."""

from __future__ import annotations

import hashlib


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def protocol_id_from_name_and_filename(protocol_name: str, filename: str) -> str:
    """Build a stable protocol id from the user-facing name and saved filename."""
    normalized_name = str(protocol_name or "").strip().lower()
    normalized_filename = str(filename or "").strip().lower()
    payload = f"name={normalized_name}\nfile={normalized_filename}"
    return f"otproto_{_digest(payload)}"


def protocol_id_from_library_key(hash_key: str) -> str:
    """Build a stable fallback id for legacy library entries."""
    payload = f"library_key={str(hash_key or '').strip().lower()}"
    return f"otproto_{_digest(payload)}"


def resolve_protocol_id(
    *,
    protocol_id: str | None = None,
    protocol_name: str = "",
    filename: str = "",
    library_key: str | None = None,
) -> str:
    existing = str(protocol_id or "").strip()
    if existing:
        return existing
    if library_key:
        return protocol_id_from_library_key(library_key)
    return protocol_id_from_name_and_filename(protocol_name, filename)


def resume_key_for_protocol(
    *,
    protocol_id: str | None = None,
    protocol_name: str = "",
    filename: str = "",
    library_key: str | None = None,
) -> str:
    resolved = resolve_protocol_id(
        protocol_id=protocol_id,
        protocol_name=protocol_name,
        filename=filename,
        library_key=library_key,
    )
    return f"resume_{resolved}"


def legacy_resume_key_from_source(protocol_name: str, source_text: str) -> str:
    """Compatibility helper for legacy content-based resume keys."""
    payload = f"{protocol_name}\n{source_text or ''}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
