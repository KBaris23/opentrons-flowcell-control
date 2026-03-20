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
import time
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
    has_pause: bool = False
    metadata: dict = field(default_factory=dict)
    requirements: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ProtocolExecutionResult:
    """Execution outcome for one protocol run or resume."""

    ok: bool
    summary: ProtocolSummary
    state: str = "failed"
    run_id: Optional[str] = None
    protocol_id: Optional[str] = None


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
        has_pause = any(
            isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Attribute) and node.func.attr == "pause")
                or (isinstance(node.func, ast.Name) and node.func.id == "pause")
            )
            for node in ast.walk(tree)
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
            has_pause=has_pause,
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
        robot_host: str | None = None,
        robot_port: int = 31950,
    ) -> tuple[bool, ProtocolSummary]:
        result = self.execute_detailed(
            protocol_path,
            source_text=source_text,
            protocol_name=protocol_name,
            mode=mode,
            data_folder=data_folder,
            robot_host=robot_host,
            robot_port=robot_port,
        )
        return result.ok, result.summary

    def execute_detailed(
        self,
        protocol_path: str | Path | None = None,
        *,
        source_text: str | None = None,
        protocol_name: str | None = None,
        mode: str = "validate",
        data_folder: Optional[Path] = None,
        robot_host: str | None = None,
        robot_port: int = 31950,
    ) -> ProtocolExecutionResult:
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
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")

        self._save_protocol_snapshot(
            source_path=summary.path,
            source_text=source,
            data_folder=data_folder,
            protocol_name=summary.protocol_name,
        )

        normalized_mode = (mode or "validate").strip().lower()
        if normalized_mode == "validate":
            self._log("[Opentrons] Validation complete (no robot execution requested).")
            return ProtocolExecutionResult(ok=True, summary=summary, state="completed")
        if normalized_mode == "robot":
            if not robot_host:
                self._log("[Opentrons] Robot run mode requires robot_host.")
                return ProtocolExecutionResult(ok=False, summary=summary, state="failed")
            return self._run_on_robot(
                summary=summary,
                source_path=summary.path,
                source_text=source,
                protocol_name=summary.protocol_name or protocol_name or Path(display_name).name,
                robot_host=str(robot_host),
                robot_port=int(robot_port),
            )
        if normalized_mode != "simulate":
            self._log(f"[Opentrons] Unsupported run mode: {mode}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")
        if not self.sdk_available:
            self._log("[Opentrons] Simulation requested but the Opentrons SDK is not installed.")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")
        if self._stop_event.is_set():
            self._log("[Opentrons] Simulation stopped before start.")
            return ProtocolExecutionResult(ok=False, summary=summary, state="stopped")

        try:
            module = self._load_module(path=summary.path, source_text=source, display_name=display_name)
            protocol = self._create_protocol_context(summary.api_level or "2.19")
            run_fn = getattr(module, "run", None)
            if not callable(run_fn):
                raise RuntimeError("Protocol module does not expose run(protocol).")
            run_fn(protocol)
            self._log("[Opentrons] Simulation completed.")
            self._log_protocol_commands(protocol)
            return ProtocolExecutionResult(ok=True, summary=summary, state="completed")
        except Exception as exc:
            self._log(f"[Opentrons] Simulation failed: {exc}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")

    def resume_run(
        self,
        *,
        protocol_name: str,
        robot_host: str,
        robot_port: int = 31950,
        run_id: str,
    ) -> ProtocolExecutionResult:
        self._stop_event.clear()
        summary = ProtocolSummary(
            path=None,
            protocol_name=protocol_name or "inline protocol",
            warnings=[],
        )
        try:
            import requests  # type: ignore[import-not-found]
        except Exception as exc:
            self._log(f"[Opentrons] Robot mode requires `requests`: {exc}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed", run_id=run_id)

        base_url = f"http://{robot_host}:{int(robot_port)}"
        headers = {"Opentrons-Version": "2"}
        self._log(f"[Opentrons] Resuming run {run_id} on {base_url}")

        try:
            play_resp = requests.post(
                f"{base_url}/runs/{run_id}/actions",
                headers=headers,
                json={"data": {"actionType": "play"}},
                timeout=30,
            )
            if play_resp.status_code >= 400:
                self._log(f"[Opentrons] Resume failed ({play_resp.status_code}): {play_resp.text}")
                return ProtocolExecutionResult(ok=False, summary=summary, state="failed", run_id=run_id)
        except Exception as exc:
            self._log(f"[Opentrons] Resume request failed: {exc}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed", run_id=run_id)

        return self._monitor_robot_run(
            requests=requests,
            base_url=base_url,
            headers=headers,
            run_id=run_id,
            summary=summary,
        )

    def _run_on_robot(
        self,
        *,
        summary: ProtocolSummary,
        source_path: Path | None,
        source_text: str,
        protocol_name: str,
        robot_host: str,
        robot_port: int,
    ) -> ProtocolExecutionResult:
        try:
            import requests  # type: ignore[import-not-found]
        except Exception as exc:
            self._log(f"[Opentrons] Robot mode requires `requests`: {exc}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")

        base_url = f"http://{robot_host}:{int(robot_port)}"
        headers = {"Opentrons-Version": "2"}
        self._log(f"[Opentrons] Robot mode target: {base_url}")

        terminal_statuses = {"stopped", "idle", "succeeded", "failed"}
        try:
            runs_resp = requests.get(f"{base_url}/runs", headers=headers, timeout=15)
            runs_resp.raise_for_status()
            runs = (runs_resp.json() or {}).get("data", []) or []
            for run in runs:
                run_id = run.get("id")
                run_status = str(run.get("status") or "").strip().lower()
                if not run_id or run_status in terminal_statuses:
                    continue
                if run_status == "paused":
                    self._log(
                        f"[Opentrons] Cannot start a new run while paused run {run_id} exists. "
                        "Resume or stop the paused run first."
                    )
                    return ProtocolExecutionResult(ok=False, summary=summary, state="failed", run_id=str(run_id))
                self._log(f"[Opentrons] Stopping existing active run {run_id} (status={run_status})")
                try:
                    stop_resp = requests.post(
                        f"{base_url}/runs/{run_id}/actions",
                        headers=headers,
                        json={"data": {"actionType": "stop"}},
                        timeout=15,
                    )
                    if stop_resp.status_code >= 400:
                        self._log(f"[Opentrons] Warning: stop action failed for {run_id}: {stop_resp.text}")
                except Exception as exc:
                    self._log(f"[Opentrons] Warning: stop action error for {run_id}: {exc}")
        except Exception as exc:
            self._log(f"[Opentrons] Warning: could not inspect existing runs: {exc}")

        if source_path is not None:
            protocol_filename = source_path.name
            with source_path.open("rb") as fh:
                files = {"files": (protocol_filename, fh, "text/x-python")}
                upload_resp = requests.post(
                    f"{base_url}/protocols",
                    headers=headers,
                    files=files,
                    timeout=60,
                )
        else:
            protocol_filename = protocol_name if protocol_name.endswith(".py") else f"{protocol_name}.py"
            files = {"files": (protocol_filename, source_text.encode("utf-8"), "text/x-python")}
            upload_resp = requests.post(
                f"{base_url}/protocols",
                headers=headers,
                files=files,
                timeout=60,
            )

        if upload_resp.status_code >= 400:
            self._log(f"[Opentrons] Protocol upload failed ({upload_resp.status_code}): {upload_resp.text}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")
        upload_json = upload_resp.json() or {}
        protocol_id = (upload_json.get("data") or {}).get("id")
        if not protocol_id:
            self._log(f"[Opentrons] Protocol upload response missing id: {upload_json}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed")
        self._log(f"[Opentrons] Protocol uploaded: id={protocol_id}")

        create_run_resp = requests.post(
            f"{base_url}/runs",
            headers=headers,
            json={"data": {"protocolId": protocol_id}},
            timeout=30,
        )
        if create_run_resp.status_code >= 400:
            self._log(f"[Opentrons] Create run failed ({create_run_resp.status_code}): {create_run_resp.text}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed", protocol_id=str(protocol_id))
        create_json = create_run_resp.json() or {}
        run_id = (create_json.get("data") or {}).get("id")
        if not run_id:
            self._log(f"[Opentrons] Create run response missing id: {create_json}")
            return ProtocolExecutionResult(ok=False, summary=summary, state="failed", protocol_id=str(protocol_id))
        self._log(f"[Opentrons] Run created: id={run_id}")

        play_resp = requests.post(
            f"{base_url}/runs/{run_id}/actions",
            headers=headers,
            json={"data": {"actionType": "play"}},
            timeout=30,
        )
        if play_resp.status_code >= 400:
            self._log(f"[Opentrons] Start run failed ({play_resp.status_code}): {play_resp.text}")
            return ProtocolExecutionResult(
                ok=False,
                summary=summary,
                state="failed",
                run_id=str(run_id),
                protocol_id=str(protocol_id),
            )
        self._log(f"[Opentrons] Run started: id={run_id}")

        result = self._monitor_robot_run(
            requests=requests,
            base_url=base_url,
            headers=headers,
            run_id=str(run_id),
            summary=summary,
        )
        result.protocol_id = str(protocol_id)
        return result

    def _monitor_robot_run(
        self,
        *,
        requests,
        base_url: str,
        headers: dict,
        run_id: str,
        summary: ProtocolSummary,
    ) -> ProtocolExecutionResult:

        seen_command_ids: set[str] = set()
        cursor: Optional[str] = None
        last_status: Optional[str] = None
        stop_sent = False
        while True:
            if self._stop_event.is_set() and not stop_sent:
                stop_sent = True
                self._log(f"[Opentrons] Stop requested: stopping run {run_id} ...")
                try:
                    stop_req = requests.post(
                        f"{base_url}/runs/{run_id}/actions",
                        headers=headers,
                        json={"data": {"actionType": "stop"}},
                        timeout=15,
                    )
                    if stop_req.status_code >= 400:
                        self._log(f"[Opentrons] Warning: stop request failed ({stop_req.status_code}): {stop_req.text}")
                except Exception as exc:
                    self._log(f"[Opentrons] Warning: stop request error: {exc}")

            cursor = self._log_robot_comments(
                requests=requests,
                base_url=base_url,
                headers=headers,
                run_id=str(run_id),
                seen_command_ids=seen_command_ids,
                cursor=cursor,
            )

            try:
                status_resp = requests.get(f"{base_url}/runs/{run_id}", headers=headers, timeout=15)
                status_resp.raise_for_status()
                run_status = str((status_resp.json() or {}).get("data", {}).get("status") or "").strip().lower()
            except Exception as exc:
                self._log(f"[Opentrons] Failed to read run status: {exc}")
                time.sleep(2.0)
                continue

            if run_status != last_status:
                self._log(f"[Opentrons] Run status: {run_status}")
                last_status = run_status

            if run_status == "succeeded":
                self._log("[Opentrons] Robot run completed successfully.")
                return ProtocolExecutionResult(ok=True, summary=summary, state="completed", run_id=run_id)
            if run_status == "paused":
                self._log("[Opentrons] Robot run paused.")
                return ProtocolExecutionResult(ok=True, summary=summary, state="paused", run_id=run_id)
            if run_status in {"failed", "stopped"}:
                self._log(f"[Opentrons] Robot run ended with status: {run_status}")
                return ProtocolExecutionResult(ok=False, summary=summary, state=run_status, run_id=run_id)

            time.sleep(2.0)

    def _log_robot_comments(
        self,
        *,
        requests,
        base_url: str,
        headers: dict,
        run_id: str,
        seen_command_ids: set[str],
        cursor: Optional[str],
    ) -> Optional[str]:
        params: dict = {"pageLength": 100}
        if cursor is not None:
            params["cursor"] = cursor
        try:
            commands_resp = requests.get(
                f"{base_url}/runs/{run_id}/commands",
                headers=headers,
                params=params,
                timeout=15,
            )
            commands_resp.raise_for_status()
            payload = commands_resp.json() or {}
        except Exception as exc:
            self._log(f"[Opentrons] Warning: could not fetch run commands: {exc}")
            return cursor

        for command in payload.get("data", []) or []:
            cmd_id = str(command.get("id") or "").strip()
            if not cmd_id or cmd_id in seen_command_ids:
                continue
            seen_command_ids.add(cmd_id)

            if str(command.get("commandType") or "").strip().lower() != "comment":
                continue
            message = str((command.get("params") or {}).get("message") or "").strip()
            cmd_status = str(command.get("status") or "").strip().lower()
            if message:
                self._log(f"[Opentrons][COMMENT][{cmd_status or '?'}] {message}")

        next_cursor = (payload.get("meta") or {}).get("cursor")
        return str(next_cursor) if next_cursor is not None else None

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
