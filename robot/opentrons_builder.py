"""Structured Opentrons protocol builder and code generator."""

from __future__ import annotations

import json
import re
from typing import Any

_STANDARD_96_TIPRACK_ORDER = tuple(
    f"{row}{column}"
    for column in range(1, 13)
    for row in "ABCDEFGH"
)
_BOTTOM_CLEARANCE_MM = 2.0


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
    tiprack_entry = next(entry for entry in cleaned_labware if entry["alias"] == tiprack_alias)
    starting_tip = str(pipette.get("starting_tip", "A1")).strip().upper() or "A1"
    if not re.fullmatch(r"^[A-Z]+[1-9][0-9]*$", starting_tip):
        raise ValueError("Starting tip must look like a tip well such as A1 or C3.")
    tip_order = tiprack_well_order(tiprack_entry["load_name"])
    if tip_order is not None and starting_tip not in tip_order:
        raise ValueError(
            f"Starting tip '{starting_tip}' is not valid for tiprack '{tiprack_entry['load_name']}'."
        )

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
        if kind in {"transfer", "aspirate", "dispense", "move_to", "blow_out"}:
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
            "starting_tip": starting_tip,
        },
        "labware": cleaned_labware,
        "steps": cleaned_steps,
    }


def spec_hash_params(spec: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps(spec, sort_keys=True))


def summarize_protocol_spec(spec: dict[str, Any]) -> str:
    meta = spec["metadata"]
    return f"{meta['protocol_name']} | {len(spec['steps'])} step(s) | {spec['pipette']['model']}"


def tiprack_well_order(load_name: str) -> tuple[str, ...] | None:
    name = str(load_name or "").strip().lower()
    if "tiprack" in name and "96" in name:
        return _STANDARD_96_TIPRACK_ORDER
    return None


def estimate_tip_usage(raw_spec: dict[str, Any]) -> dict[str, Any]:
    spec = normalize_protocol_spec(raw_spec)
    pipette = spec["pipette"]
    steps = spec["steps"]
    tiprack_entry = next(entry for entry in spec["labware"] if entry["alias"] == pipette["tiprack_alias"])
    tiprack_load_name = tiprack_entry["load_name"]
    tip_order = tiprack_well_order(tiprack_load_name)

    tips_used = 0
    explicit_tip_well_steps = 0
    for step in steps:
        kind = step["kind"]
        if kind == "transfer" and step.get("new_tip") in {"once", "always"}:
            tips_used += 1
        elif kind == "pick_up_tip":
            tips_used += 1
            if step.get("source_well"):
                explicit_tip_well_steps += 1

    warnings: list[str] = []
    if tip_order is None:
        warnings.append(
            "Tip budget estimate supports standard 96-well tipracks only; verify remaining tips manually."
        )
        return {
            "tiprack_alias": pipette["tiprack_alias"],
            "tiprack_load_name": tiprack_load_name,
            "starting_tip": pipette["starting_tip"],
            "end_tip": None,
            "tips_used": tips_used,
            "available_tips": None,
            "remaining_tips": None,
            "over_capacity": False,
            "explicit_tip_well_steps": explicit_tip_well_steps,
            "warnings": warnings,
        }

    start_index = tip_order.index(pipette["starting_tip"])
    available_tips = len(tip_order) - start_index
    remaining_tips = available_tips - tips_used
    over_capacity = remaining_tips < 0
    if over_capacity:
        warnings.append(
            f"Tip usage estimate exceeds the remaining rack: {tips_used} pickup(s) requested but only "
            f"{available_tips} tip(s) remain from {pipette['starting_tip']} to {tip_order[-1]}."
        )
    if explicit_tip_well_steps:
        warnings.append(
            "Explicit pick_up_tip wells are counted as one pickup each; verify they do not reuse skipped tips."
        )

    next_tip = None
    if 0 <= start_index + tips_used < len(tip_order):
        next_tip = tip_order[start_index + tips_used]

    return {
        "tiprack_alias": pipette["tiprack_alias"],
        "tiprack_load_name": tiprack_load_name,
        "starting_tip": pipette["starting_tip"],
        "end_tip": tip_order[-1],
        "next_tip": next_tip,
        "tips_used": tips_used,
        "available_tips": available_tips,
        "remaining_tips": max(remaining_tips, 0),
        "over_capacity": over_capacity,
        "explicit_tip_well_steps": explicit_tip_well_steps,
        "warnings": warnings,
    }


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
        ]
    )
    if pipette["starting_tip"] != "A1":
        lines.append(f"    pipette.starting_tip = {tiprack_var}[{pipette['starting_tip']!r}]")
    lines.append("")

    if not steps:
        lines.append("    protocol.comment('No steps defined.')")

    def location_expr(var_name: str, well_name: str, location: str) -> str:
        if location == "bottom":
            return f"{var_name}[{well_name!r}].bottom({_BOTTOM_CLEARANCE_MM:g})"
        if location == "center":
            return f"{var_name}[{well_name!r}].center()"
        return f"{var_name}[{well_name!r}].top()"

    for step in steps:
        kind = step["kind"]
        if kind == "transfer":
            src = alias_map[step["source_alias"]]
            dst = alias_map[step["dest_alias"]]
            location = step.get("location", "top")
            lines.append(
                f"    pipette.transfer({step['volume_ul']:g}, {location_expr(src, step['source_well'], location)}, {location_expr(dst, step['dest_well'], location)}, new_tip={step['new_tip']!r})"
            )
        elif kind == "aspirate":
            src = alias_map[step["source_alias"]]
            location = step.get("location", "top")
            lines.append(
                f"    pipette.aspirate({step['volume_ul']:g}, {location_expr(src, step['source_well'], location)})"
            )
        elif kind == "dispense":
            dst = alias_map[step["dest_alias"]]
            location = step.get("location", "top")
            lines.append(
                f"    pipette.dispense({step['volume_ul']:g}, {location_expr(dst, step['dest_well'], location)})"
            )
        elif kind == "move_to":
            src = alias_map[step["source_alias"]]
            location = step.get("location", "top")
            lines.append(f"    pipette.move_to({location_expr(src, step['source_well'], location)})")
        elif kind == "blow_out":
            src = alias_map[step["source_alias"]]
            location = step.get("location", "top")
            lines.append(f"    pipette.blow_out({location_expr(src, step['source_well'], location)})")
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
