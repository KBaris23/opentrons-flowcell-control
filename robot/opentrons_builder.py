"""Structured Opentrons protocol builder and code generator."""

from __future__ import annotations

import json
import re
from typing import Any


def normalize_identifier(value: str, *, fallback: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9_]+", "_", (value or "").strip())
    text = re.sub(r"_+", "_", text).strip("_")
    if not text:
        text = fallback
    if text[0].isdigit():
        text = f"{fallback}_{text}"
    return text.lower()


def normalize_protocol_spec(raw: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(raw.get("metadata") or {})
    pipette = dict(raw.get("pipette") or {})
    labware = [dict(entry or {}) for entry in (raw.get("labware") or [])]
    steps = [dict(step or {}) for step in (raw.get("steps") or [])]

    tiprack_alias = normalize_identifier(pipette.get("tiprack_alias", "tips"), fallback="tips")
    cleaned_labware = []
    seen_aliases: set[str] = set()
    for entry in labware:
        alias = normalize_identifier(entry.get("alias", ""), fallback="labware")
        if alias in seen_aliases:
            raise ValueError(f"Duplicate labware alias: {alias}")
        seen_aliases.add(alias)
        load_name = str(entry.get("load_name", "")).strip()
        slot = str(entry.get("slot", "")).strip()
        if not load_name or not slot:
            raise ValueError("Each labware entry needs alias, load name, and slot.")
        cleaned_labware.append({"alias": alias, "load_name": load_name, "slot": slot})

    if tiprack_alias not in {entry["alias"] for entry in cleaned_labware}:
        raise ValueError("Tiprack alias must match one of the loaded labware aliases.")

    cleaned_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(steps, start=1):
        kind = str(step.get("kind", "")).strip().lower()
        if not kind:
            raise ValueError(f"Step {idx} is missing a kind.")
        cleaned = {"kind": kind}
        if kind in {"transfer", "aspirate", "dispense"}:
            cleaned["volume_ul"] = float(step.get("volume_ul", 0))
        if kind in {"transfer", "aspirate", "move_to", "blow_out"}:
            cleaned["source_alias"] = normalize_identifier(step.get("source_alias", ""), fallback="labware")
            cleaned["source_well"] = str(step.get("source_well", "")).strip().upper()
        if kind in {"transfer", "dispense"}:
            cleaned["dest_alias"] = normalize_identifier(step.get("dest_alias", ""), fallback="labware")
            cleaned["dest_well"] = str(step.get("dest_well", "")).strip().upper()
        if kind == "transfer":
            cleaned["new_tip"] = str(step.get("new_tip", "once")).strip().lower() or "once"
        if kind == "move_to":
            cleaned["location"] = str(step.get("location", "top")).strip().lower() or "top"
        if kind == "delay":
            cleaned["seconds"] = float(step.get("seconds", 0))
        if kind == "comment":
            comment = str(step.get("comment", "")).strip()
            if not comment:
                raise ValueError(f"Step {idx} comment cannot be empty.")
            cleaned["comment"] = comment
        if kind == "pause":
            message = str(step.get("message", "")).strip()
            if not message:
                raise ValueError(f"Step {idx} pause message cannot be empty.")
            cleaned["message"] = message
        if kind in {"pick_up_tip", "drop_tip"}:
            cleaned["source_alias"] = normalize_identifier(
                step.get("source_alias", tiprack_alias),
                fallback=tiprack_alias,
            )
            cleaned["source_well"] = str(step.get("source_well", "")).strip().upper()
        cleaned_steps.append(cleaned)

    return {
        "metadata": {
            "protocol_name": str(metadata.get("protocol_name", "Generated Protocol")).strip() or "Generated Protocol",
            "author": str(metadata.get("author", "Opentrons Flowcell Console")).strip() or "Opentrons Flowcell Console",
            "description": str(metadata.get("description", "Generated from the UI builder.")).strip() or "Generated from the UI builder.",
            "api_level": str(metadata.get("api_level", "2.19")).strip() or "2.19",
            "robot_type": str(metadata.get("robot_type", "OT-2")).strip() or "OT-2",
        },
        "pipette": {
            "model": str(pipette.get("model", "p20_single_gen2")).strip() or "p20_single_gen2",
            "mount": str(pipette.get("mount", "left")).strip().lower() or "left",
            "tiprack_alias": tiprack_alias,
        },
        "labware": cleaned_labware,
        "steps": cleaned_steps,
    }


def spec_hash_params(spec: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(spec, sort_keys=True))


def summarize_protocol_spec(spec: dict[str, Any]) -> str:
    meta = spec["metadata"]
    return f"{meta['protocol_name']} | {len(spec['steps'])} step(s) | {spec['pipette']['model']}"


def generate_protocol_source(raw_spec: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    spec = normalize_protocol_spec(raw_spec)
    meta = spec["metadata"]
    pipette = spec["pipette"]
    labware = spec["labware"]
    steps = spec["steps"]

    lines: list[str] = [
        '"""Generated Opentrons protocol."""',
        "",
        "from opentrons import protocol_api",
        "",
        "metadata = {",
        f'    "protocolName": {meta["protocol_name"]!r},',
        f'    "author": {meta["author"]!r},',
        f'    "description": {meta["description"]!r},',
        '    "source": "opentrons-flowcell-control-ui",',
        f'    "apiLevel": {meta["api_level"]!r},',
        "}",
        "",
        "requirements = {",
        f'    "robotType": {meta["robot_type"]!r},',
        "}",
        "",
        "",
        "def run(protocol: protocol_api.ProtocolContext):",
    ]

    alias_map = {entry["alias"]: normalize_identifier(entry["alias"], fallback="labware") for entry in labware}
    for entry in labware:
        lines.append(
            f"    {alias_map[entry['alias']]} = protocol.load_labware({entry['load_name']!r}, {entry['slot']!r})"
        )
    tiprack_var = alias_map[pipette["tiprack_alias"]]
    lines.extend(
        [
            f"    pipette = protocol.load_instrument({pipette['model']!r}, {pipette['mount']!r}, tip_racks=[{tiprack_var}])",
            "",
        ]
    )

    if not steps:
        lines.append("    protocol.comment('No steps defined.')")

    for step in steps:
        kind = step["kind"]
        if kind == "transfer":
            src = alias_map[step["source_alias"]]
            dst = alias_map[step["dest_alias"]]
            lines.append(
                f"    pipette.transfer({step['volume_ul']:g}, {src}[{step['source_well']!r}], {dst}[{step['dest_well']!r}], new_tip={step['new_tip']!r})"
            )
        elif kind == "aspirate":
            src = alias_map[step["source_alias"]]
            lines.append(
                f"    pipette.aspirate({step['volume_ul']:g}, {src}[{step['source_well']!r}])"
            )
        elif kind == "dispense":
            dst = alias_map[step["dest_alias"]]
            lines.append(
                f"    pipette.dispense({step['volume_ul']:g}, {dst}[{step['dest_well']!r}])"
            )
        elif kind == "move_to":
            src = alias_map[step["source_alias"]]
            location = step.get("location", "top")
            if location == "bottom":
                lines.append(f"    pipette.move_to({src}[{step['source_well']!r}].bottom())")
            elif location == "center":
                lines.append(f"    pipette.move_to({src}[{step['source_well']!r}].center())")
            else:
                lines.append(f"    pipette.move_to({src}[{step['source_well']!r}].top())")
        elif kind == "blow_out":
            src = alias_map[step["source_alias"]]
            lines.append(f"    pipette.blow_out({src}[{step['source_well']!r}])")
        elif kind == "delay":
            lines.append(f"    protocol.delay(seconds={step['seconds']:g})")
        elif kind == "comment":
            lines.append(f"    protocol.comment({step['comment']!r})")
        elif kind == "pause":
            lines.append(f"    protocol.pause({step['message']!r})")
        elif kind == "pick_up_tip":
            src = alias_map[step["source_alias"]]
            if step["source_well"]:
                lines.append(f"    pipette.pick_up_tip({src}[{step['source_well']!r}])")
            else:
                lines.append("    pipette.pick_up_tip()")
        elif kind == "drop_tip":
            src = alias_map[step["source_alias"]]
            if step["source_well"]:
                lines.append(f"    pipette.drop_tip({src}[{step['source_well']!r}])")
            else:
                lines.append("    pipette.drop_tip()")
        elif kind == "home":
            lines.append("    protocol.home()")
        else:
            raise ValueError(f"Unsupported step kind: {kind}")

    lines.append("")
    return "\n".join(lines), spec
