"""Helpers for inspecting and running Opentrons protocol files.

This module keeps Opentrons support optional. The GUI can always inspect and
queue protocol files, while simulation only activates when the Opentrons SDK is
installed in the local Python environment.
"""

from __future__ import annotations

import ast
import importlib.util
import shutil
import threading
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional

try:
    from opentrons import execute as _ot_execute  # type: ignore[import-not-found]
except Exception:
    _ot_execute = None

try:
    from opentrons import simulate as _ot_simulate  # type: ignore[import-not-found]
except Exception:
    _ot_simulate = None


LogCallback = Callable[[str], None]


@dataclass(slots=True)
class ProtocolSummary:
    """Parsed metadata for one protocol file."""

    path: Optional[Path]
    protocol_name: str
    author: str = ""
    description: str = ""
    api_level: str = ""
    robot_type: str = ""
    has_run: bool = False
    metadata: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class OpentronsProtocolRunner:
    """Inspect and execute protocol files with optional SDK-backed simulation."""

    def __init__(self, log_callback: LogCallback = print):
        self._log = log_callback
        self._stop_event = threading.Event()

    @property
    def sdk_available(self) -> bool:
        return (_ot_simulate is not None) or (_ot_execute is not None)

    def stop(self) -> None:
        self._stop_event.set()

    def inspect_protocol(
        self,
        protocol_path: str | Path | None = None,
        *,
        source_text: str | None = None,
        protocol_name: str | None = None,
    ) -> ProtocolSummary:
        source, path, display_name = self._resolve_source(
            protocol_path=protocol_path,
            source_text=source_text,
            protocol_name=protocol_name,
        )
        tree = ast.parse(source, filename=display_name)

        metadata = self._extract_literal_dict(tree, "metadata")
        requirements = self._extract_literal_dict(tree, "requirements")
        has_run = any(
            isinstance(node, ast.FunctionDef) and node.name == "run"
            for node in tree.body
        )

        warnings: list[str] = []
        if not metadata:
            warnings.append("No metadata dict found.")
        if not has_run:
            warnings.append("Protocol is missing run(protocol).")
        if metadata and "apiLevel" not in metadata:
            warnings.append("metadata.apiLevel is missing.")

        return ProtocolSummary(
            path=path,
            protocol_name=str(metadata.get("protocolName") or protocol_name or Path(display_name).stem),
            author=str(metadata.get("author") or ""),
            description=str(metadata.get("description") or ""),
            api_level=str(metadata.get("apiLevel") or ""),
            robot_type=str(requirements.get("robotType") or ""),
            has_run=has_run,
            metadata=metadata,
            requirements=requirements,
            warnings=warnings,
        )

    def execute(
        self,
        protocol_path: str | Path | None = None,
        *,
        source_text: str | None = None,
        protocol_name: str | None = None,
        mode: str = "validate",
        data_folder: Optional[Path] = None,
    ) -> tuple[bool, ProtocolSummary]:
        self._stop_event.clear()
        source, path, display_name = self._resolve_source(
            protocol_path=protocol_path,
            source_text=source_text,
            protocol_name=protocol_name,
        )
        summary = self.inspect_protocol(
            path,
            source_text=None if path is not None else source,
            protocol_name=protocol_name or Path(display_name).stem,
        )
        self._log(
            "[Opentrons] "
            f"{summary.protocol_name} | api={summary.api_level or '?'} | "
            f"robot={summary.robot_type or '?'}"
        )
        if summary.description:
            self._log(f"[Opentrons] {summary.description}")
        for warning in summary.warnings:
            self._log(f"[Opentrons] Warning: {warning}")

        if not summary.has_run:
            return False, summary

        self._save_protocol_snapshot(
            source_path=summary.path,
            source_text=source,
            data_folder=data_folder,
            protocol_name=summary.protocol_name,
        )

        normalized_mode = (mode or "validate").strip().lower()
        if normalized_mode == "validate":
            self._log("[Opentrons] Validation complete (no robot execution requested).")
            return True, summary
        if normalized_mode != "simulate":
            self._log(f"[Opentrons] Unsupported run mode: {mode}")
            return False, summary
        if not self.sdk_available:
            self._log("[Opentrons] Simulation requested but the Opentrons SDK is not installed.")
            return False, summary
        if self._stop_event.is_set():
            self._log("[Opentrons] Simulation stopped before start.")
            return False, summary

        try:
            module = self._load_module(path=summary.path, source_text=source, display_name=display_name)
            protocol = self._create_protocol_context(summary.api_level or "2.19")
            run_fn = getattr(module, "run", None)
            if not callable(run_fn):
                raise RuntimeError("Protocol module does not expose run(protocol).")
            run_fn(protocol)
            self._log("[Opentrons] Simulation completed.")
            self._log_protocol_commands(protocol)
            return True, summary
        except Exception as exc:
            self._log(f"[Opentrons] Simulation failed: {exc}")
            return False, summary

    @staticmethod
    def _extract_literal_dict(tree: ast.AST, name: str) -> dict:
        for node in getattr(tree, "body", []):
            if not isinstance(node, ast.Assign):
                continue
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == name:
                    try:
                        value = ast.literal_eval(node.value)
                    except Exception:
                        return {}
                    return value if isinstance(value, dict) else {}
        return {}

    @staticmethod
    def _load_module(
        path: Path | None,
        *,
        source_text: str,
        display_name: str,
    ) -> ModuleType:
        if path is not None:
            module_name = f"_opentrons_protocol_{path.stem}_{abs(hash(path))}"
            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Could not load protocol module from {path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module

        module_name = f"_opentrons_protocol_inline_{abs(hash(display_name + source_text))}"
        module = ModuleType(module_name)
        module.__file__ = display_name
        exec(compile(source_text, display_name, "exec"), module.__dict__)
        return module

    @staticmethod
    def _resolve_source(
        *,
        protocol_path: str | Path | None,
        source_text: str | None,
        protocol_name: str | None,
    ) -> tuple[str, Optional[Path], str]:
        if source_text is not None:
            display_name = protocol_name or "inline_opentrons_protocol.py"
            return source_text, None, display_name
        if protocol_path is None:
            raise ValueError("Provide protocol_path or source_text.")
        path = Path(protocol_path).expanduser().resolve()
        source = path.read_text(encoding="utf-8")
        return source, path, str(path)

    @staticmethod
    def _create_protocol_context(api_level: str):
        errors: list[str] = []
        if _ot_simulate is not None and hasattr(_ot_simulate, "get_protocol_api"):
            try:
                return _ot_simulate.get_protocol_api(api_level)
            except Exception as exc:
                errors.append(f"simulate.get_protocol_api failed: {exc}")
        if _ot_execute is not None and hasattr(_ot_execute, "get_protocol_api"):
            try:
                return _ot_execute.get_protocol_api(api_level)
            except Exception as exc:
                errors.append(f"execute.get_protocol_api failed: {exc}")
        details = "; ".join(errors) if errors else "no API context provider available"
        raise RuntimeError(details)

    def _log_protocol_commands(self, protocol) -> None:
        commands_fn = getattr(protocol, "commands", None)
        if not callable(commands_fn):
            return
        try:
            commands = commands_fn()
        except Exception:
            return
        if not isinstance(commands, (list, tuple)):
            return
        for command in commands:
            self._log(f"[Opentrons] {command}")

    def _save_protocol_snapshot(
        self,
        *,
        source_path: Optional[Path],
        source_text: str,
        data_folder: Optional[Path],
        protocol_name: str,
    ) -> None:
        if data_folder is None:
            return
        try:
            used_dir = Path(data_folder) / "opentrons_protocols_used"
            used_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            if source_path is not None:
                dst = used_dir / f"{source_path.stem}_{ts}{source_path.suffix}"
                shutil.copy2(source_path, dst)
            else:
                slug = self._slugify(protocol_name) or "inline_protocol"
                dst = used_dir / f"{slug}_{ts}.py"
                dst.write_text(source_text, encoding="utf-8")
            self._log(f"[Opentrons] Protocol snapshot: {dst}")
        except Exception as exc:
            self._log(f"[Opentrons] Warning: could not save protocol snapshot: {exc}")

    @staticmethod
    def _slugify(value: str) -> str:
        text = "".join(ch.lower() if ch.isalnum() else "_" for ch in (value or "").strip())
        while "__" in text:
            text = text.replace("__", "_")
        return text.strip("_")
