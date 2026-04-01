"""gui/tab_opentrons.py - Opentrons protocol browser and builder."""

from __future__ import annotations

import copy
import re
import subprocess
import sys
import threading
import hashlib
from pathlib import Path
import json
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from config import (
    OPENTRONS_DEFAULT_API_PORT,
    OPENTRONS_DEFAULT_HOST,
    OPENTRONS_DEFAULT_RUN_MODE,
    OPENTRONS_PROTOCOLS_DIR,
)
from robot import (
    OpentronsProtocolRunner,
    estimate_tip_usage,
    generate_protocol_source,
    summarize_protocol_spec,
)
from robot.opentrons_builder import normalize_protocol_spec, spec_hash_params, tiprack_well_order


class OpentronsTab:
    """File-based and UI-native Opentrons protocol workflows."""

    _OTHER_LABWARE_SENTINEL = "Other..."
    _LOCAL_LABWARE_DEF_ROOT = (
        Path(".venv")
        / "Lib"
        / "site-packages"
        / "opentrons_shared_data"
        / "data"
        / "labware"
        / "definitions"
        / "2"
    )

    _MODE_LABELS = {
        "validate": "Validate Only",
        "simulate": "Simulate (SDK required)",
        "robot": "Run on OT-2 (HTTP API)",
    }

    _STEP_KINDS = [
        "transfer",
        "move_to",
        "aspirate",
        "dispense",
        "blow_out",
        "delay",
        "pick_up_tip",
        "drop_tip",
        "home",
        "comment",
        "pause",
    ]

    _COMMON_LABWARE_LOAD_NAME_PRESETS = [
        "opentrons_96_filtertiprack_20ul",
        "opentrons_24_tuberack_nest_2ml_snapcap",
        "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap",
        "opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap",
        "opentrons_6_tuberack_falcon_50ml_conical",
        "opentrons_6_tuberack_nest_50ml_conical",
        "opentrons_15_tuberack_falcon_15ml_conical",
        "opentrons_15_tuberack_nest_15ml_conical",
        "opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical",
        "opentrons_10_tuberack_nest_4x50ml_6x15ml_conical",
        "opentrons_96_filtertiprack_200ul",
        "opentrons_96_filtertiprack_1000ul",
    ]
    _PIPETTE_MAX_VOLUME_UL = {
        "p20_single_gen2": 20.0,
        "p300_single_gen2": 300.0,
        "p1000_single_gen2": 1000.0,
    }
    _GENERIC_WELL_RE = re.compile(r"^([A-P])([1-9]|1[0-9]|2[0-4])$")
    _MAX_TRANSFER_VOLUME_UL = 50000.0
    _TIP_WELL_OPTIONS = [
        f"{row}{column}"
        for column in range(1, 13)
        for row in "ABCDEFGH"
    ]

    def __init__(self, parent_frame, session, on_add_to_queue, root: tk.Tk):
        self._frame = parent_frame
        self._session = session
        self._add_to_queue = on_add_to_queue
        self._root = root
        self._runner = OpentronsProtocolRunner(log_callback=self.log)

        self._log_text: tk.Text | None = None
        self._preview_text: tk.Text | None = None
        self._protocol_map: dict[str, Path] = {}
        self._labware_rows: list[dict] = []
        self._step_rows: list[dict] = []
        self._step_clipboard: list[dict] = []
        self._selected_labware_index: int | None = None
        self._selected_step_index: int | None = None
        self._available_labware_load_names = self._discover_labware_load_names()
        self._all_labware_load_names = self._labware_load_name_options_static()

        self._build()
        self._seed_builder_defaults()
        self._load_protocol_files()
        self._refresh_labware_name_options()
        self._refresh_labware_tree()
        self._refresh_step_tree()
        self._apply_tracked_starting_tip(force=True)
        self.preview_builder_protocol()

    def _build(self) -> None:
        container = ttk.Frame(self._frame)
        container.pack(fill="both", expand=True, padx=8, pady=8)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1, minsize=260)

        self._main_notebook = ttk.Notebook(container)
        self._main_notebook.grid(row=0, column=0, sticky="nsew")

        file_tab = ttk.Frame(self._main_notebook)
        builder_tab = ttk.Frame(self._main_notebook)
        self._main_notebook.add(file_tab, text="Protocol Config")
        self._main_notebook.add(builder_tab, text="Protocol Builder")

        self._build_file_tab(file_tab)
        self._build_builder_tab(builder_tab)

    def _summary_frame(self, parent) -> None:
        summary = ttk.LabelFrame(parent, text="Protocol Summary")
        summary.grid(row=0, column=0, sticky="ew")
        summary.columnconfigure(1, weight=3)
        summary.columnconfigure(3, weight=1)
        summary.columnconfigure(5, weight=1)

        self._summary_vars = {
            "name": tk.StringVar(value="-"),
            "api": tk.StringVar(value="-"),
            "robot": tk.StringVar(value="-"),
            "author": tk.StringVar(value="-"),
            "description": tk.StringVar(value="-"),
            "warnings": tk.StringVar(value="-"),
        }
        compact_fields = [
            (0, 0, "Name:", "name"),
            (0, 2, "API:", "api"),
            (0, 4, "Robot:", "robot"),
            (1, 0, "Author:", "author"),
        ]
        for row, col, label, key in compact_fields:
            ttk.Label(summary, text=label).grid(row=row, column=col, padx=4, pady=2, sticky="e")
            ttk.Label(summary, textvariable=self._summary_vars[key], justify="left").grid(
                row=row,
                column=col + 1,
                padx=(0, 8),
                pady=2,
                sticky="w",
            )

        ttk.Label(summary, text="Description:").grid(row=2, column=0, padx=4, pady=2, sticky="ne")
        ttk.Label(
            summary,
            textvariable=self._summary_vars["description"],
            wraplength=700,
            justify="left",
        ).grid(row=2, column=1, columnspan=5, padx=(0, 8), pady=2, sticky="w")

        ttk.Label(summary, text="Warnings:").grid(row=3, column=0, padx=4, pady=2, sticky="ne")
        ttk.Label(
            summary,
            textvariable=self._summary_vars["warnings"],
            wraplength=700,
            justify="left",
        ).grid(row=3, column=1, columnspan=5, padx=(0, 8), pady=(2, 4), sticky="w")

    def _build_file_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        self._summary_frame(parent)

        body = ttk.Panedwindow(parent, orient="vertical")
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        config_wrap = ttk.Frame(body)
        config_wrap.columnconfigure(0, weight=1)
        log_wrap = ttk.Frame(body)
        log_wrap.columnconfigure(0, weight=1)
        log_wrap.rowconfigure(0, weight=1)
        body.add(config_wrap, weight=3)
        body.add(log_wrap, weight=1)

        top = ttk.LabelFrame(config_wrap, text="Saved / Bundled Protocols")
        top.grid(row=0, column=0, sticky="ew")
        top.columnconfigure(1, weight=1)
        top.columnconfigure(3, weight=1)

        ttk.Label(top, text="Available protocol:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        self._var_bundled = tk.StringVar()
        self._combo_bundled = ttk.Combobox(top, textvariable=self._var_bundled, state="readonly")
        self._combo_bundled.grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        self._combo_bundled.bind("<<ComboboxSelected>>", self._on_file_selected)
        ttk.Button(top, text="Refresh", command=self._load_protocol_files).grid(
            row=0,
            column=2,
            padx=4,
            pady=4,
            sticky="w",
        )

        ttk.Label(top, text="Protocol file:").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        self._var_path = tk.StringVar()
        ttk.Entry(top, textvariable=self._var_path).grid(
            row=1,
            column=1,
            columnspan=2,
            padx=4,
            pady=4,
            sticky="ew",
        )
        ttk.Button(top, text="Browse", command=self._browse_protocol).grid(
            row=1,
            column=3,
            padx=4,
            pady=4,
            sticky="w",
        )

        ttk.Label(top, text="Run mode:").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        self._var_mode = tk.StringVar(value=self._mode_label(OPENTRONS_DEFAULT_RUN_MODE))
        self._combo_mode = ttk.Combobox(
            top,
            textvariable=self._var_mode,
            values=[self._mode_label("validate"), self._mode_label("simulate"), self._mode_label("robot")],
            state="readonly",
        )
        self._combo_mode.grid(row=2, column=1, padx=4, pady=4, sticky="w")

        sdk_text = "available" if self._runner.sdk_available else "not installed"
        sdk_color = "green" if self._runner.sdk_available else "#666"
        ttk.Label(top, text=f"Opentrons SDK: {sdk_text}", foreground=sdk_color).grid(
            row=2, column=2, padx=4, pady=4, sticky="w"
        )

        ttk.Label(top, text="Robot host/IP:").grid(row=3, column=0, padx=4, pady=4, sticky="e")
        self._var_robot_host = tk.StringVar(value=str(OPENTRONS_DEFAULT_HOST))
        ttk.Entry(top, textvariable=self._var_robot_host, width=24).grid(
            row=3, column=1, padx=4, pady=4, sticky="w"
        )
        self._btn_ping = ttk.Button(top, text="Check Connectivity", command=self._check_robot_connectivity)
        self._btn_ping.grid(
            row=3, column=2, padx=4, pady=4, sticky="w"
        )
        self._var_ping_status = tk.StringVar(value="Connectivity: idle")
        ttk.Label(top, textvariable=self._var_ping_status, foreground="#666").grid(
            row=3, column=3, padx=4, pady=4, sticky="w"
        )

        ttk.Label(top, text="Robot API port:").grid(row=4, column=0, padx=4, pady=4, sticky="e")
        self._var_robot_port = tk.StringVar(value=str(int(OPENTRONS_DEFAULT_API_PORT)))
        ttk.Entry(top, textvariable=self._var_robot_port, width=10).grid(
            row=4, column=1, padx=4, pady=4, sticky="w"
        )

        btns = ttk.Frame(top)
        btns.grid(row=5, column=0, columnspan=4, padx=4, pady=(6, 4), sticky="w")
        ttk.Button(btns, text="Inspect", command=self.inspect_current_file).pack(side="left", padx=4)
        ttk.Button(btns, text="Run Now", command=self.run_file_now).pack(side="left", padx=4)
        ttk.Button(btns, text="Add to Queue", command=self.add_file_to_queue).pack(side="left", padx=4)
        ttk.Button(btns, text="Add Resume", command=self.add_file_resume_to_queue).pack(side="left", padx=4)
        ttk.Button(btns, text="Add Home", command=self.add_home_to_queue).pack(side="left", padx=4)
        ttk.Button(btns, text="Home OT-2", command=self.home_robot_now).pack(side="left", padx=4)
        ttk.Button(btns, text="Load Into Builder", command=self.load_current_file_into_builder).pack(side="left", padx=4)
        ttk.Button(btns, text="Delete From Library", command=self.delete_current_library_protocol).pack(side="left", padx=4)

        tips = ttk.LabelFrame(config_wrap, text="Quick Help")
        tips.grid(row=1, column=0, sticky="ew", pady=(6, 0))
        tips.columnconfigure(0, weight=1)
        ttk.Label(
            tips,
            text=(
                "Inspect: read the file and summarize it.  "
                "Preview: show builder-generated Python code.  "
                "Validate: check only, no simulation or robot run.  "
                "Use Load Into Builder to edit a saved generated library protocol."
            ),
            wraplength=900,
            justify="left",
        ).grid(row=0, column=0, padx=8, pady=6, sticky="w")

        log_frame = ttk.LabelFrame(log_wrap, text="Log")
        log_frame.grid(row=0, column=0, sticky="nsew")
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log_text = tk.Text(log_frame, height=10, state="disabled")
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(4, 6), pady=6)
        self._log_text.configure(yscrollcommand=scroll.set)

    def _build_builder_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self._builder_mode_var = tk.StringVar(value=self._mode_label(OPENTRONS_DEFAULT_RUN_MODE))
        self._builder_protocol_name = tk.StringVar(value="Generated Protocol")
        self._builder_author = tk.StringVar(value="Opentrons Flowcell Console")
        self._builder_description = tk.StringVar(value="Generated from the UI builder.")
        self._builder_api = tk.StringVar(value="2.19")
        self._builder_robot = tk.StringVar(value="OT-2")
        self._builder_pipette_model = tk.StringVar(value="p20_single_gen2")
        self._builder_mount = tk.StringVar(value="left")
        self._builder_tiprack_alias = tk.StringVar(value="tips")
        self._builder_starting_tip = tk.StringVar(value="A1")
        self._builder_use_dual_pipettes = tk.BooleanVar(value=False)
        self._builder_secondary_pipette_model = tk.StringVar(value="p20_single_gen2")
        self._builder_secondary_mount = tk.StringVar(value="right")
        self._builder_secondary_tiprack_alias = tk.StringVar(value="tips_right")
        self._builder_secondary_starting_tip = tk.StringVar(value="A1")
        self._builder_auto_tip_tracking = tk.BooleanVar(value=True)
        self._builder_tip_budget_var = tk.StringVar(
            value="Tip budget: 0 pickup(s) requested; 96 tip(s) available from A1 to H12; 96 tip(s) left."
        )

        notebook = ttk.Notebook(parent)
        notebook.grid(row=0, column=0, sticky="nsew", padx=4, pady=4)

        setup_tab = ttk.Frame(notebook, padding=4)
        steps_tab = ttk.Frame(notebook, padding=4)
        preview_tab = ttk.Frame(notebook, padding=4)
        notebook.add(setup_tab, text="Setup")
        notebook.add(steps_tab, text="Steps")
        notebook.add(preview_tab, text="Generated Preview")

        self._build_builder_setup_tab(setup_tab)
        self._build_builder_steps_tab(steps_tab)
        self._build_builder_preview_tab(preview_tab)

    def _build_builder_setup_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        setup_pane = ttk.Panedwindow(parent, orient="vertical")
        setup_pane.grid(row=0, column=0, sticky="nsew")

        meta_wrap = ttk.Frame(setup_pane)
        meta_wrap.columnconfigure(0, weight=1)
        deck_wrap = ttk.Frame(setup_pane)
        deck_wrap.columnconfigure(0, weight=1)
        deck_wrap.rowconfigure(0, weight=1)
        setup_pane.add(meta_wrap, weight=2)
        setup_pane.add(deck_wrap, weight=3)

        meta = ttk.LabelFrame(meta_wrap, text="Protocol Metadata")
        meta.grid(row=0, column=0, sticky="ew")
        for col in range(6):
            meta.columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)

        ttk.Label(meta, text="Name:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_protocol_name).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="Author:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_author).grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="Run mode:").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_mode_var,
            values=[self._mode_label("validate"), self._mode_label("simulate"), self._mode_label("robot")],
            state="readonly",
        ).grid(row=0, column=5, padx=4, pady=4, sticky="ew")

        ttk.Label(meta, text="Description:").grid(row=1, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_description).grid(row=1, column=1, columnspan=5, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="API level:").grid(row=2, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_api, width=10).grid(row=2, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(meta, text="Robot:").grid(row=2, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_robot, width=10).grid(row=2, column=3, padx=4, pady=4, sticky="w")
        ttk.Label(meta, text="Pipette:").grid(row=2, column=4, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_pipette_model,
            values=["p20_single_gen2", "p300_single_gen2", "p1000_single_gen2"],
            state="readonly",
        ).grid(row=2, column=5, padx=4, pady=4, sticky="ew")

        ttk.Label(meta, text="Pipette side:").grid(row=3, column=4, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_mount,
            values=["left", "right"],
            state="readonly",
            width=10,
        ).grid(row=3, column=5, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="Tiprack alias:").grid(row=3, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_tiprack_alias, width=12).grid(row=3, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(meta, text="Starting tip:").grid(row=3, column=2, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_starting_tip,
            values=self._TIP_WELL_OPTIONS,
            width=10,
        ).grid(row=3, column=3, padx=4, pady=4, sticky="w")
        ttk.Checkbutton(
            meta,
            text="Use both pipettes in protocol",
            variable=self._builder_use_dual_pipettes,
            command=self.preview_builder_protocol,
        ).grid(row=4, column=0, columnspan=2, padx=4, pady=(0, 4), sticky="w")
        ttk.Label(meta, text="2nd pipette:").grid(row=4, column=2, padx=4, pady=(0, 4), sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_secondary_pipette_model,
            values=["p20_single_gen2", "p300_single_gen2", "p1000_single_gen2"],
            state="readonly",
        ).grid(row=4, column=3, padx=4, pady=(0, 4), sticky="ew")
        ttk.Label(meta, text="2nd side:").grid(row=4, column=4, padx=4, pady=(0, 4), sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_secondary_mount,
            values=["left", "right"],
            state="readonly",
            width=10,
        ).grid(row=4, column=5, padx=4, pady=(0, 4), sticky="ew")
        ttk.Label(meta, text="2nd tiprack alias:").grid(row=5, column=0, padx=4, pady=(0, 4), sticky="e")
        ttk.Entry(meta, textvariable=self._builder_secondary_tiprack_alias, width=12).grid(row=5, column=1, padx=4, pady=(0, 4), sticky="w")
        ttk.Label(meta, text="2nd starting tip:").grid(row=5, column=2, padx=4, pady=(0, 4), sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_secondary_starting_tip,
            values=self._TIP_WELL_OPTIONS,
            width=10,
        ).grid(row=5, column=3, padx=4, pady=(0, 4), sticky="w")
        ttk.Checkbutton(
            meta,
            text="Auto-advance tip",
            variable=self._builder_auto_tip_tracking,
            command=self._apply_tracked_starting_tip,
        ).grid(row=6, column=2, padx=4, pady=(0, 4), sticky="w")
        ttk.Button(meta, text="Reset Tip Tracker", command=self._reset_builder_tip_tracker).grid(
            row=6, column=3, padx=4, pady=(0, 4), sticky="w"
        )
        ttk.Label(
            meta,
            textvariable=self._builder_tip_budget_var,
            foreground="#666",
            wraplength=900,
            justify="left",
        ).grid(row=7, column=0, columnspan=6, padx=4, pady=(0, 4), sticky="w")

        self._build_labware_panel(deck_wrap)

        action_btns = ttk.Frame(parent)
        action_btns.grid(row=1, column=0, sticky="w", pady=(8, 0))
        ttk.Button(action_btns, text="Preview", command=self.preview_builder_protocol).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Run Now", command=self.run_builder_now).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add to Queue", command=self.add_builder_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add Resume", command=self.add_builder_resume_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Save to Library", command=self.save_builder_to_library).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Load Selected File", command=self.load_current_file_into_builder).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Clear Builder", command=self.clear_builder_form).pack(side="left", padx=3)

    def _build_builder_steps_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)
        self._build_steps_panel(parent)

    def _build_builder_preview_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        preview_frame = ttk.LabelFrame(parent, text="Generated Protocol Preview")
        preview_frame.grid(row=0, column=0, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)

        self._preview_text = tk.Text(preview_frame, height=20, state="disabled")
        self._preview_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self._preview_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(4, 6), pady=6)
        self._preview_text.configure(yscrollcommand=scroll.set)

        action_btns = ttk.Frame(parent)
        action_btns.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(action_btns, text="Preview", command=self.preview_builder_protocol).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Run Now", command=self.run_builder_now).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add to Queue", command=self.add_builder_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add Resume", command=self.add_builder_resume_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Save to Library", command=self.save_builder_to_library).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Load Selected File", command=self.load_current_file_into_builder).pack(side="left", padx=3)

    def _build_labware_panel(self, parent) -> None:
        frame = ttk.LabelFrame(parent, text="Deck Labware")
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(1, weight=1)

        form = ttk.Frame(frame)
        form.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        for col in range(6):
            form.columnconfigure(col, weight=1)

        self._labware_alias_var = tk.StringVar(value="tips")
        self._labware_name_var = tk.StringVar(value="opentrons_96_filtertiprack_20ul")
        self._labware_slot_var = tk.StringVar(value="4")

        ttk.Label(form, text="Alias").grid(row=0, column=0, padx=2, pady=2, sticky="w")
        ttk.Entry(form, textvariable=self._labware_alias_var).grid(row=1, column=0, padx=2, pady=2, sticky="ew")
        ttk.Label(form, text="Load name").grid(row=0, column=1, padx=2, pady=2, sticky="w")
        self._combo_labware_name = ttk.Combobox(
            form,
            textvariable=self._labware_name_var,
            values=self._labware_load_name_options(),
        )
        self._combo_labware_name.grid(row=1, column=1, columnspan=3, padx=2, pady=2, sticky="ew")
        self._combo_labware_name.bind("<<ComboboxSelected>>", self._on_labware_name_selected)
        ttk.Label(form, text="Slot").grid(row=0, column=4, padx=2, pady=2, sticky="w")
        ttk.Entry(form, textvariable=self._labware_slot_var, width=8).grid(row=1, column=4, padx=2, pady=2, sticky="ew")

        btns = ttk.Frame(form)
        btns.grid(row=1, column=5, padx=2, pady=2, sticky="e")
        ttk.Button(btns, text="Add / Update", command=self._upsert_labware).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete", command=self._delete_labware).pack(side="left", padx=2)
        ttk.Label(
            form,
            text="Load name must match an Opentrons labware definition exactly. Pick a preset or type your own.",
            foreground="#666",
            wraplength=760,
            justify="left",
        ).grid(row=2, column=0, columnspan=6, padx=2, pady=(2, 0), sticky="w")

        cols = ("Alias", "Load Name", "Slot")
        self._labware_tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for col, width in (("Alias", 120), ("Load Name", 260), ("Slot", 60)):
            self._labware_tree.heading(col, text=col)
            self._labware_tree.column(col, width=width)
        self._labware_tree.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._labware_tree.bind("<<TreeviewSelect>>", self._on_labware_selected)

    def _build_steps_panel(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(0, weight=1)

        self._step_warning_var = tk.StringVar(value="")

        main_pane = ttk.Panedwindow(parent, orient="vertical")
        main_pane.grid(row=0, column=0, sticky="nsew")

        controls_wrap = ttk.Frame(main_pane)
        controls_wrap.columnconfigure(0, weight=1)
        middle_wrap = ttk.Frame(main_pane)
        middle_wrap.columnconfigure(0, weight=1)
        middle_wrap.rowconfigure(0, weight=1)
        main_pane.add(controls_wrap, weight=3)
        main_pane.add(middle_wrap, weight=5)

        controls = ttk.LabelFrame(controls_wrap, text="Step Builder")
        controls.grid(row=0, column=0, sticky="ew")
        for col in range(9):
            controls.columnconfigure(col, weight=1)

        self._step_kind_var = tk.StringVar(value="transfer")
        self._step_volume_var = tk.StringVar(value="1")
        self._step_source_alias_var = tk.StringVar(value="source")
        self._step_source_well_var = tk.StringVar(value="A1")
        self._step_dest_alias_var = tk.StringVar(value="dest")
        self._step_dest_well_var = tk.StringVar(value="A2")
        self._step_location_var = tk.StringVar(value="top")
        self._step_new_tip_var = tk.StringVar(value="once")
        self._step_pipette_key_var = tk.StringVar(value="primary")
        self._step_seconds_var = tk.StringVar(value="1")
        self._step_comment_var = tk.StringVar(value="")

        fields = [
            ("Kind", self._step_kind_var, self._STEP_KINDS),
            ("Volume (uL)", self._step_volume_var, None),
            ("Source Alias", self._step_source_alias_var, None),
            ("Source Well", self._step_source_well_var, None),
            ("Dest Alias", self._step_dest_alias_var, None),
            ("Dest Well", self._step_dest_well_var, None),
            ("Location", self._step_location_var, ["top", "center", "bottom"]),
            ("New Tip", self._step_new_tip_var, ["once", "always", "never"]),
            ("Pipette", self._step_pipette_key_var, ["primary", "secondary"]),
        ]
        for idx, (label, var, values) in enumerate(fields):
            ttk.Label(controls, text=label).grid(row=0, column=idx, padx=2, pady=2, sticky="w")
            if values:
                ttk.Combobox(controls, textvariable=var, values=values, state="readonly").grid(
                    row=1, column=idx, padx=2, pady=2, sticky="ew"
                )
            else:
                ttk.Entry(controls, textvariable=var).grid(row=1, column=idx, padx=2, pady=2, sticky="ew")

        ttk.Label(controls, text="Delay (s)").grid(row=2, column=0, padx=2, pady=2, sticky="w")
        ttk.Entry(controls, textvariable=self._step_seconds_var).grid(row=3, column=0, padx=2, pady=2, sticky="ew")
        ttk.Label(controls, text="Text / Message").grid(row=2, column=1, padx=2, pady=2, sticky="w")
        ttk.Entry(controls, textvariable=self._step_comment_var).grid(
            row=3,
            column=1,
            columnspan=7,
            padx=2,
            pady=2,
            sticky="ew",
        )

        step_btns = ttk.Frame(controls)
        step_btns.grid(row=4, column=0, columnspan=8, padx=2, pady=(4, 2), sticky="w")
        ttk.Button(step_btns, text="Add Step", command=self._add_step).pack(side="left", padx=2)
        ttk.Button(step_btns, text="Update Selected", command=self._update_step).pack(side="left", padx=2)
        ttk.Button(step_btns, text="Delete Step", command=self._delete_step).pack(side="left", padx=2)
        ttk.Button(step_btns, text="Clear Steps", command=self._clear_steps).pack(side="left", padx=2)
        ttk.Label(
            controls,
            textvariable=self._step_warning_var,
            foreground="#b00020",
            wraplength=900,
            justify="left",
        ).grid(row=5, column=0, columnspan=8, padx=2, pady=(2, 0), sticky="w")

        tree_frame = ttk.Frame(middle_wrap)
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.grid(row=0, column=0, sticky="nsew", pady=(6, 0))

        cols = ("#", "Kind", "Details")
        self._step_tree = ttk.Treeview(tree_frame, columns=cols, show="headings", height=8, selectmode="extended")
        for col, width in (("#", 50), ("Kind", 120), ("Details", 520)):
            self._step_tree.heading(col, text=col)
            self._step_tree.column(col, width=width)
        self._step_tree.grid(row=0, column=0, sticky="nsew")
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._step_tree.yview)
        tree_scroll.grid(row=0, column=1, sticky="ns", padx=(4, 0))
        self._step_tree.configure(yscrollcommand=tree_scroll.set)
        self._step_tree.bind("<<TreeviewSelect>>", self._on_step_selected)
        self._step_tree.bind("<Button-3>", self._show_step_ctx)
        self._step_tree.bind("<Control-c>", lambda _event: self._copy_selected_steps())
        self._step_tree.bind("<Control-v>", lambda _event: self._paste_steps_after_selected())
        self._step_tree.bind("<Control-d>", lambda _event: self._duplicate_selected_steps())

        self._step_ctx = tk.Menu(self._root, tearoff=0)
        self._step_ctx.add_command(label="Copy", command=self._copy_selected_steps)
        self._step_ctx.add_command(label="Paste After", command=self._paste_steps_after_selected)
        self._step_ctx.add_command(label="Duplicate", command=self._duplicate_selected_steps)
        self._step_ctx.add_separator()
        self._step_ctx.add_command(label="Delete", command=self._delete_step)

        action_btns = ttk.Frame(parent)
        action_btns.grid(row=1, column=0, sticky="w", pady=(6, 0))
        ttk.Button(action_btns, text="Preview", command=self.preview_builder_protocol).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Run Now", command=self.run_builder_now).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add to Queue", command=self.add_builder_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add Resume", command=self.add_builder_resume_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Save to Library", command=self.save_builder_to_library).pack(side="left", padx=3)

        edit_btns = ttk.Frame(parent)
        edit_btns.grid(row=2, column=0, sticky="w", pady=(6, 0))
        ttk.Button(edit_btns, text="Copy", command=self._copy_selected_steps).pack(side="left", padx=3)
        ttk.Button(edit_btns, text="Paste After", command=self._paste_steps_after_selected).pack(side="left", padx=3)
        ttk.Button(edit_btns, text="Duplicate", command=self._duplicate_selected_steps).pack(side="left", padx=3)
        ttk.Label(
            edit_btns,
            text="Shortcuts: Ctrl+C copy, Ctrl+V paste after, Ctrl+D duplicate",
            foreground="#666",
        ).pack(side="left", padx=(8, 0))

    def _seed_builder_defaults(self) -> None:
        self._labware_rows = []
        self._step_rows = []
        self._labware_alias_var.set("")
        self._labware_name_var.set("")
        self._labware_slot_var.set("")
        self._builder_tiprack_alias.set("")
        self._builder_starting_tip.set("A1")
        self._step_source_alias_var.set("")
        self._step_source_well_var.set("")
        self._step_dest_alias_var.set("")
        self._step_dest_well_var.set("")
        self._step_comment_var.set("")
        self._builder_use_dual_pipettes.set(False)
        self._builder_secondary_pipette_model.set("p20_single_gen2")
        self._builder_secondary_mount.set("right")
        self._builder_secondary_tiprack_alias.set("tips_right")
        self._builder_secondary_starting_tip.set("A1")
        self._set_tip_budget_message("Tip budget: add a tiprack and preview the builder to estimate remaining tips.")

    def _labware_load_name_options(self) -> list[str]:
        names = {
            str(entry.get("load_name", "")).strip()
            for entry in self._labware_rows
            if str(entry.get("load_name", "")).strip()
        }
        common = [
            name
            for name in self._COMMON_LABWARE_LOAD_NAME_PRESETS
            if name in self._all_labware_load_names or name in names
        ]
        extras = sorted(
            name
            for name in names.union(self._available_labware_load_names)
            if name not in common
        )
        options = [*common]
        if extras:
            options.append(self._OTHER_LABWARE_SENTINEL)
        return options

    @classmethod
    def _discover_labware_load_names(cls) -> set[str]:
        root = cls._LOCAL_LABWARE_DEF_ROOT
        if not root.exists():
            return set()
        names: set[str] = set()
        for definition_dir in root.iterdir():
            if not definition_dir.is_dir():
                continue
            latest_definition: Path | None = None
            for candidate in sorted(definition_dir.glob("*.json")):
                latest_definition = candidate
            if latest_definition is None:
                continue
            try:
                payload = json.loads(latest_definition.read_text(encoding="utf-8"))
            except Exception:
                continue
            load_name = str(payload.get("parameters", {}).get("loadName", "")).strip()
            if load_name:
                names.add(load_name)
        return names

    @classmethod
    def _fifty_ml_rack_variants(cls) -> tuple[str, ...]:
        return tuple(
            name
            for name in cls._labware_load_name_options_static()
            if "50ml_conical" in name
        )

    @classmethod
    def _labware_load_name_options_static(cls) -> set[str]:
        names = set(cls._COMMON_LABWARE_LOAD_NAME_PRESETS)
        names.update(cls._discover_labware_load_names())
        return names

    @classmethod
    def _labware_name_warning(cls, load_name: str) -> str | None:
        name = (load_name or "").strip()
        if not name:
            return None
        known = cls._labware_load_name_options_static()
        if name not in known:
            return (
                "This load name is not present in the local Opentrons labware list. "
                "A mismatched load name can make the OT-2 move with the wrong rack geometry."
            )
        if "tuberack" in name and "50ml" in name:
            variants = ", ".join(cls._fifty_ml_rack_variants())
            return (
                "50 mL tube racks have multiple Opentrons definitions. "
                f"Double-check that the physical rack matches this exact load name: {name}. "
                f"Local 50 mL options: {variants}."
            )
        return None

    def _other_labware_load_names(self) -> list[str]:
        common = set(self._COMMON_LABWARE_LOAD_NAME_PRESETS)
        dynamic = {
            str(entry.get("load_name", "")).strip()
            for entry in self._labware_rows
            if str(entry.get("load_name", "")).strip()
        }
        return sorted((self._all_labware_load_names | dynamic) - common)

    def _on_labware_name_selected(self, _event=None) -> None:
        if (self._labware_name_var.get() or "").strip() != self._OTHER_LABWARE_SENTINEL:
            return
        selected = self._choose_other_labware_load_name()
        self._labware_name_var.set(selected or "")

    def _choose_other_labware_load_name(self) -> str | None:
        options = self._other_labware_load_names()
        if not options:
            messagebox.showinfo("No Additional Labware", "No additional labware definitions were found.")
            return None

        dialog = tk.Toplevel(self._root)
        dialog.title("Choose Other Labware")
        dialog.transient(self._root)
        dialog.grab_set()
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)
        dialog.minsize(560, 420)

        filter_var = tk.StringVar()
        selection: dict[str, str | None] = {"value": None}

        ttk.Label(
            dialog,
            text="Choose another labware definition. The main dropdown keeps the common current options on top.",
            wraplength=520,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 6))

        body = ttk.Frame(dialog)
        body.grid(row=1, column=0, sticky="nsew", padx=10, pady=4)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(1, weight=1)

        ttk.Entry(body, textvariable=filter_var).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        listbox = tk.Listbox(body, activestyle="dotbox")
        listbox.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(body, orient="vertical", command=listbox.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        listbox.configure(yscrollcommand=scroll.set)

        def refresh(*_args) -> None:
            query = (filter_var.get() or "").strip().lower()
            visible = [name for name in options if query in name.lower()]
            listbox.delete(0, tk.END)
            for name in visible:
                listbox.insert(tk.END, name)
            if visible:
                listbox.selection_set(0)
                listbox.activate(0)

        def accept(_event=None) -> None:
            sel = listbox.curselection()
            if not sel:
                return
            selection["value"] = str(listbox.get(sel[0]))
            dialog.destroy()

        def cancel() -> None:
            dialog.destroy()

        filter_var.trace_add("write", refresh)
        listbox.bind("<Double-Button-1>", accept)
        listbox.bind("<Return>", accept)

        buttons = ttk.Frame(dialog)
        buttons.grid(row=2, column=0, sticky="e", padx=10, pady=(6, 10))
        ttk.Button(buttons, text="Cancel", command=cancel).pack(side="left", padx=4)
        ttk.Button(buttons, text="Select", command=accept).pack(side="left", padx=4)

        refresh()
        dialog.wait_window()
        return selection["value"]

    def _refresh_labware_name_options(self) -> None:
        if hasattr(self, "_combo_labware_name"):
            self._combo_labware_name.configure(values=self._labware_load_name_options())

    def _set_step_warning(self, message: str) -> None:
        if hasattr(self, "_step_warning_var"):
            self._step_warning_var.set(message)

    def _set_tip_budget_message(self, message: str) -> None:
        if hasattr(self, "_builder_tip_budget_var"):
            self._builder_tip_budget_var.set(message)

    def _builder_tracker_context(self, spec: dict) -> tuple[str, dict] | tuple[None, None]:
        try:
            normalized = normalize_protocol_spec(spec)
        except Exception:
            return None, None
        if normalized.get("secondary_pipette"):
            return None, None
        pipette = normalized["pipette"]
        tiprack_entry = next(
            (entry for entry in normalized["labware"] if entry["alias"] == pipette["tiprack_alias"]),
            None,
        )
        if tiprack_entry is None:
            return None, None
        if tiprack_well_order(tiprack_entry["load_name"]) is None:
            return None, None
        context = {
            "robot_type": normalized["metadata"]["robot_type"],
            "pipette_model": pipette["model"],
            "mount": pipette["mount"],
            "tiprack_alias": pipette["tiprack_alias"],
            "tiprack_load_name": tiprack_entry["load_name"],
            "tiprack_slot": tiprack_entry["slot"],
        }
        tracker_key = "|".join(
            [
                context["robot_type"],
                context["pipette_model"],
                context["mount"],
                context["tiprack_load_name"],
                context["tiprack_slot"],
            ]
        )
        return tracker_key, context

    def _tracked_builder_tip_state(self, spec: dict | None = None) -> tuple[str, dict, dict] | tuple[None, None, None]:
        tracker_key, context = self._builder_tracker_context(spec or self._builder_spec())
        if not tracker_key:
            return None, None, None
        state = self._session.opentrons_tip_registry.snapshot(tracker_key)
        return tracker_key, context, state

    def _apply_tracked_starting_tip(self, *, force: bool = False) -> None:
        if not bool(self._builder_auto_tip_tracking.get()):
            return
        tracker_key, _context, state = self._tracked_builder_tip_state()
        if not tracker_key or not state:
            return
        next_tip = str(state.get("next_tip") or "").strip().upper()
        if not next_tip:
            return
        current = str(self._builder_starting_tip.get() or "").strip().upper()
        if force or current in {"", "A1"}:
            self._builder_starting_tip.set(next_tip)

    def _record_builder_tip_usage(self, spec: dict, *, event_name: str) -> dict | None:
        tracker_key, context, _state = self._tracked_builder_tip_state(spec)
        if not tracker_key or not context:
            return None
        usage = estimate_tip_usage(spec)
        if usage.get("available_tips") is None or usage.get("over_capacity"):
            return None
        tips_used = int(usage.get("tips_used", 0) or 0)
        if tips_used <= 0:
            return None
        return self._session.opentrons_tip_registry.record_protocol(
            tracker_key=tracker_key,
            protocol_name=str(spec.get("metadata", {}).get("protocol_name", "")),
            starting_tip=str(usage.get("starting_tip") or ""),
            tips_used=tips_used,
            next_tip=str(usage.get("next_tip") or ""),
            context=context,
            event_name=event_name,
        )

    def _reset_builder_tip_tracker(self) -> None:
        tracker_key, context, _state = self._tracked_builder_tip_state()
        if not tracker_key or not context:
            messagebox.showwarning(
                "Tip Tracker Unavailable",
                "Add a standard 96-well tiprack to the builder before resetting tip tracking.",
            )
            return
        state = self._session.opentrons_tip_registry.reset_tiprack(
            tracker_key=tracker_key,
            next_tip="A1",
            context=context,
            reason="manual builder reset",
        )
        self._builder_starting_tip.set(str(state.get("next_tip") or "A1"))
        self.preview_builder_protocol()
        self.log("[Opentrons Tip Tracker] Reset tracked starting tip to A1 for the current tiprack setup.")

    @staticmethod
    def _merge_warning_lists(base_warnings: list[str], extra_warnings: list[str]) -> list[str]:
        merged: list[str] = []
        for warning in [*(base_warnings or []), *(extra_warnings or [])]:
            if warning and warning not in merged:
                merged.append(warning)
        return merged

    def _builder_tip_usage_warnings(self, spec: dict | None = None) -> list[str]:
        try:
            usage = estimate_tip_usage(spec or self._builder_spec())
        except Exception as exc:
            self._set_tip_budget_message(f"Tip budget: unavailable ({exc})")
            return []

        warnings = list(usage.get("warnings") or [])
        per_pipette = usage.get("per_pipette") or {}
        if per_pipette and len(per_pipette) > 1:
            segments = []
            for key, bucket in per_pipette.items():
                if bucket.get("available_tips") is None:
                    segments.append(f"{key}: manual check")
                    continue
                text = (
                    f"{key}: {bucket.get('tips_used', 0)} pickup(s), "
                    f"{bucket.get('available_tips', 0)} available from {bucket.get('starting_tip')}"
                )
                if bucket.get("next_tip"):
                    text += f", next {bucket.get('next_tip')}"
                segments.append(text)
            self._set_tip_budget_message("Tip budget: " + " | ".join(segments))
            return warnings
        available_tips = usage.get("available_tips")
        if available_tips is None:
            self._set_tip_budget_message(
                f"Tip budget: {usage.get('tips_used', 0)} pickup(s) requested. "
                "Estimate unavailable for this tiprack."
            )
            return warnings

        message = (
            f"Tip budget: {usage.get('tips_used', 0)} pickup(s) requested; "
            f"{available_tips} tip(s) available from {usage.get('starting_tip')} to {usage.get('end_tip')}; "
            f"{usage.get('remaining_tips', 0)} tip(s) left."
        )
        if usage.get("next_tip"):
            message += f" Next suggested tip: {usage.get('next_tip')}."
        elif usage.get("tips_used", 0):
            message += " This protocol would consume the remaining tracked tips."
        if usage.get("over_capacity"):
            short_by = max(int(usage.get("tips_used", 0)) - int(available_tips), 0)
            message += f" Short by {short_by} tip(s)."
        self._set_tip_budget_message(message)
        return warnings

    def _known_labware_aliases(self) -> set[str]:
        aliases = {
            str(entry.get("alias", "")).strip()
            for entry in self._labware_rows
            if str(entry.get("alias", "")).strip()
        }
        tiprack_alias = (self._builder_tiprack_alias.get() or "").strip()
        if tiprack_alias:
            aliases.add(tiprack_alias)
        if bool(self._builder_use_dual_pipettes.get()):
            secondary_tiprack_alias = (self._builder_secondary_tiprack_alias.get() or "").strip()
            if secondary_tiprack_alias:
                aliases.add(secondary_tiprack_alias)
        return aliases

    def _validate_alias(self, alias: str, field_name: str) -> None:
        if not alias:
            raise ValueError(f"{field_name} is required.")
        if alias not in self._known_labware_aliases():
            known = ", ".join(sorted(self._known_labware_aliases())) or "(none)"
            raise ValueError(f"{field_name} '{alias}' does not exist. Known aliases: {known}.")

    def _validate_well(self, well: str, field_name: str) -> None:
        if not well:
            raise ValueError(f"{field_name} is required.")
        if not self._GENERIC_WELL_RE.fullmatch(well):
            raise ValueError(f"{field_name} '{well}' is not a sane well name. Use something like A1 through P24.")

    def _validate_step(self, step: dict) -> None:
        kind = step.get("kind", "")
        pipette_key = str(step.get("pipette_key", "primary")).strip().lower() or "primary"
        if pipette_key == "secondary" and not bool(self._builder_use_dual_pipettes.get()):
            raise ValueError("Secondary pipette selected for this step, but dual-pipette mode is not enabled.")
        pipette_model = (
            self._builder_secondary_pipette_model.get()
            if pipette_key == "secondary"
            else self._builder_pipette_model.get()
        ) or ""
        pipette_model = pipette_model.strip()
        pipette_max_ul = self._PIPETTE_MAX_VOLUME_UL.get(pipette_model, 0.0)

        if kind in {"transfer", "aspirate", "dispense"}:
            volume_ul = float(step.get("volume_ul", 0))
            if volume_ul <= 0:
                raise ValueError("Volume must be greater than 0 uL.")
            if kind in {"aspirate", "dispense"} and pipette_max_ul and volume_ul > pipette_max_ul:
                raise ValueError(f"{kind.title()} volume {volume_ul:g} uL exceeds the selected pipette capacity ({pipette_max_ul:g} uL).")
            if kind == "transfer" and volume_ul > self._MAX_TRANSFER_VOLUME_UL:
                raise ValueError(f"Transfer volume {volume_ul:g} uL is too large for a sane builder step. Split it into smaller steps.")

        if kind in {"transfer", "aspirate", "move_to", "blow_out"}:
            self._validate_alias(str(step.get("source_alias", "")).strip(), "Source alias")
            self._validate_well(str(step.get("source_well", "")).strip().upper(), "Source well")

        if kind in {"transfer", "dispense"}:
            self._validate_alias(str(step.get("dest_alias", "")).strip(), "Dest alias")
            self._validate_well(str(step.get("dest_well", "")).strip().upper(), "Dest well")

        if kind in {"pick_up_tip", "drop_tip"}:
            default_tiprack_alias = (
                (self._builder_secondary_tiprack_alias.get() or "").strip()
                if pipette_key == "secondary"
                else (self._builder_tiprack_alias.get() or "").strip()
            )
            alias = str(step.get("source_alias", "")).strip() or default_tiprack_alias
            step["source_alias"] = alias
            self._validate_alias(alias, "Tiprack alias")
            well = str(step.get("source_well", "")).strip().upper()
            if well:
                self._validate_well(well, "Tip well")
                step["source_well"] = well

        if kind in {"transfer", "aspirate", "dispense", "move_to", "blow_out"}:
            location = str(step.get("location", "")).strip().lower()
            if location not in {"top", "center", "bottom"}:
                raise ValueError("Location must be top, center, or bottom.")

        if kind == "transfer":
            new_tip = str(step.get("new_tip", "")).strip().lower()
            if new_tip not in {"once", "always", "never"}:
                raise ValueError("New Tip must be once, always, or never.")

        if kind == "delay":
            seconds = float(step.get("seconds", 0))
            if seconds < 0:
                raise ValueError("Delay must be 0 seconds or more.")
            if seconds > 86400:
                raise ValueError("Delay is too large for a sane builder step.")

    def log(self, msg: str) -> None:
        if self._log_text is None:
            return

        def _append() -> None:
            assert self._log_text is not None
            self._log_text.configure(state="normal")
            self._log_text.insert("end", msg + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self._root.after(0, _append)

    def _mode_label(self, mode_key: str) -> str:
        return self._MODE_LABELS.get(mode_key, mode_key)

    def _mode_key(self, label: str) -> str:
        selected = (label or "").strip()
        for key, value in self._MODE_LABELS.items():
            if value == selected:
                return key
        return OPENTRONS_DEFAULT_RUN_MODE

    def _protocol_file_label(self, path: Path, root: Path) -> str:
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        try:
            summary = self._runner.inspect_protocol(path)
            name = (summary.protocol_name or "").strip() or path.stem
        except Exception:
            name = path.stem
        return f"{name} | {rel}"

    def _load_protocol_files(self) -> None:
        proto_dir = Path(OPENTRONS_PROTOCOLS_DIR)
        proto_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(
            path.resolve()
            for path in proto_dir.rglob("*.py")
            if path.name != "__init__.py"
        )
        self._protocol_map = {}
        for path in files:
            label = self._protocol_file_label(path, proto_dir)
            self._protocol_map[label] = path
        labels = list(self._protocol_map)
        self._combo_bundled.configure(values=labels)
        if labels:
            if not self._var_bundled.get() or self._var_bundled.get() not in self._protocol_map:
                self._var_bundled.set(labels[0])
            self._on_file_selected()

    def _on_file_selected(self, _event=None) -> None:
        path = self._protocol_map.get((self._var_bundled.get() or "").strip())
        if path is not None:
            self._var_path.set(str(path))

    def _browse_protocol(self) -> None:
        path = filedialog.askopenfilename(
            title="Select Opentrons protocol",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
            initialdir=str(Path(OPENTRONS_PROTOCOLS_DIR)),
        )
        if path:
            self._var_path.set(path)

    def _check_robot_connectivity(self) -> None:
        host = (self._var_robot_host.get() or "").strip()
        if not host:
            messagebox.showwarning("Missing Host", "Enter the OT-2 hostname or IP address first.")
            return

        self._var_ping_status.set(f"Connectivity: checking {host} ...")
        self._btn_ping.configure(state="disabled")
        self.log(f"[Opentrons] Checking connectivity to {host} ...")

        def _worker() -> None:
            if sys.platform.startswith("win"):
                cmd = ["ping", "-n", "1", "-w", "2000", host]
            else:
                cmd = ["ping", "-c", "1", "-W", "2", host]
            try:
                proc = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    errors="replace",
                    timeout=6,
                    check=False,
                )
                output = (proc.stdout or proc.stderr or "").strip()
                lines = [line.strip() for line in output.splitlines() if line.strip()]
                snippet = " | ".join(lines[-2:]) if lines else "(no output)"
                if proc.returncode == 0:
                    self.log(f"[Opentrons] Connectivity OK: {host}")
                    self.log(f"[Opentrons] ping: {snippet}")
                    self._root.after(
                        0,
                        lambda: (
                            self._var_ping_status.set(f"Connectivity: OK ({host})"),
                            self._btn_ping.configure(state="normal"),
                            messagebox.showinfo("Connectivity Check", f"Ping OK for {host}"),
                        ),
                    )
                else:
                    self.log(f"[Opentrons] Connectivity FAILED: {host}")
                    self.log(f"[Opentrons] ping: {snippet}")
                    self._root.after(
                        0,
                        lambda: (
                            self._var_ping_status.set(f"Connectivity: FAILED ({host})"),
                            self._btn_ping.configure(state="normal"),
                            messagebox.showwarning("Connectivity Check", f"Ping failed for {host}"),
                        ),
                    )
            except Exception as exc:
                self.log(f"[Opentrons] Connectivity check error for {host}: {exc}")
                self._root.after(
                    0,
                    lambda e=exc: (
                        self._var_ping_status.set(f"Connectivity: error ({host})"),
                        self._btn_ping.configure(state="normal"),
                        messagebox.showerror("Connectivity Check", f"Error checking {host}:\n{e}"),
                    ),
                )

        threading.Thread(target=_worker, daemon=True).start()

    def _current_protocol_path(self) -> Path:
        raw = (self._var_path.get() or "").strip()
        if not raw:
            raise ValueError("Select a protocol file first.")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Protocol file not found: {path}")
        return path

    def clear_builder_form(self) -> None:
        self._builder_protocol_name.set("Generated Protocol")
        self._builder_author.set("Opentrons Flowcell Console")
        self._builder_description.set("")
        self._builder_api.set("2.19")
        self._builder_robot.set("OT-2")
        self._builder_pipette_model.set("p20_single_gen2")
        self._builder_mount.set("left")
        self._builder_tiprack_alias.set("")
        self._builder_starting_tip.set("A1")
        self._builder_use_dual_pipettes.set(False)
        self._builder_secondary_pipette_model.set("p20_single_gen2")
        self._builder_secondary_mount.set("right")
        self._builder_secondary_tiprack_alias.set("tips_right")
        self._builder_secondary_starting_tip.set("A1")
        self._labware_alias_var.set("")
        self._labware_name_var.set("")
        self._labware_slot_var.set("")
        self._step_kind_var.set("transfer")
        self._step_volume_var.set("1")
        self._step_source_alias_var.set("")
        self._step_source_well_var.set("")
        self._step_dest_alias_var.set("")
        self._step_dest_well_var.set("")
        self._step_location_var.set("top")
        self._step_new_tip_var.set("once")
        self._step_pipette_key_var.set("primary")
        self._step_seconds_var.set("1")
        self._step_comment_var.set("")
        self._labware_rows = []
        self._step_rows = []
        self._selected_labware_index = None
        self._selected_step_index = None
        self._set_tip_budget_message("Tip budget: add a tiprack and preview the builder to estimate remaining tips.")
        self._refresh_labware_name_options()
        self._refresh_labware_tree()
        self._refresh_step_tree()
        self._apply_tracked_starting_tip(force=False)
        self.preview_builder_protocol()

    def _apply_builder_summary_placeholder(self, warning: str = "Builder is empty.") -> None:
        self._summary_vars["name"].set((self._builder_protocol_name.get() or "").strip() or "-")
        self._summary_vars["api"].set((self._builder_api.get() or "").strip() or "-")
        self._summary_vars["robot"].set((self._builder_robot.get() or "").strip() or "-")
        self._summary_vars["author"].set((self._builder_author.get() or "").strip() or "-")
        self._summary_vars["description"].set((self._builder_description.get() or "").strip() or "-")
        self._summary_vars["warnings"].set(warning)

    def load_current_file_into_builder(self) -> None:
        try:
            path = self._current_protocol_path()
        except Exception as exc:
            messagebox.showerror("Load Failed", str(exc))
            return
        found = self._session.opentrons_registry.entry_for_path(path)
        if found is None:
            messagebox.showerror(
                "Load Failed",
                "This protocol is not a saved generated builder protocol in the library.",
            )
            return
        _key, entry = found
        params = dict(entry.get("params") or {})
        if str(entry.get("kind") or "").strip() != "generated_ui_protocol" or not params:
            messagebox.showerror(
                "Load Failed",
                "Only generated builder protocols can be loaded back into the builder.",
            )
            return
        self._load_builder_spec(params)
        try:
            self._main_notebook.select(1)
        except Exception:
            pass
        self.log(f"[Opentrons Builder] Loaded {path.name} into builder.")

    def delete_current_library_protocol(self) -> None:
        try:
            path = self._current_protocol_path()
        except Exception as exc:
            messagebox.showerror("Delete Failed", str(exc))
            return
        if not messagebox.askyesno("Delete Protocol", f"Delete {path.name} from the Opentrons library?"):
            return
        if not self._session.opentrons_registry.delete_protocol(path):
            messagebox.showerror("Delete Failed", "Selected file is not a deletable library protocol.")
            return
        self._load_protocol_files()
        self._var_path.set("")
        self.log(f"[Opentrons Library] Deleted {path.name}")

    def _load_builder_spec(self, raw_spec: dict) -> None:
        spec = {
            "metadata": dict(raw_spec.get("metadata") or {}),
            "pipette": dict(raw_spec.get("pipette") or {}),
            "secondary_pipette": dict(raw_spec.get("secondary_pipette") or {}),
            "labware": [dict(entry or {}) for entry in (raw_spec.get("labware") or [])],
            "steps": [dict(step or {}) for step in (raw_spec.get("steps") or [])],
        }
        meta = spec["metadata"]
        pipette = spec["pipette"]
        secondary = spec["secondary_pipette"]
        self._builder_protocol_name.set(str(meta.get("protocol_name", "Generated Protocol")))
        self._builder_author.set(str(meta.get("author", "Opentrons Flowcell Console")))
        self._builder_description.set(str(meta.get("description", "")))
        self._builder_api.set(str(meta.get("api_level", "2.19")))
        self._builder_robot.set(str(meta.get("robot_type", "OT-2")))
        self._builder_pipette_model.set(str(pipette.get("model", "p20_single_gen2")))
        self._builder_mount.set(str(pipette.get("mount", "left")))
        self._builder_tiprack_alias.set(str(pipette.get("tiprack_alias", "")))
        self._builder_starting_tip.set(str(pipette.get("starting_tip", "A1")))
        self._builder_use_dual_pipettes.set(bool(secondary))
        self._builder_secondary_pipette_model.set(str(secondary.get("model", "p20_single_gen2")))
        self._builder_secondary_mount.set(str(secondary.get("mount", "right")))
        self._builder_secondary_tiprack_alias.set(str(secondary.get("tiprack_alias", "tips_right")))
        self._builder_secondary_starting_tip.set(str(secondary.get("starting_tip", "A1")))
        self._labware_rows = spec["labware"]
        self._step_rows = spec["steps"]
        self._selected_labware_index = None
        self._selected_step_index = None
        self._labware_alias_var.set("")
        self._labware_name_var.set("")
        self._labware_slot_var.set("")
        self._step_source_alias_var.set("")
        self._step_source_well_var.set("")
        self._step_dest_alias_var.set("")
        self._step_dest_well_var.set("")
        self._step_comment_var.set("")
        self._refresh_labware_name_options()
        self._refresh_labware_tree()
        self._refresh_step_tree()
        self.preview_builder_protocol()

    def inspect_current_file(self):
        try:
            summary = self._runner.inspect_protocol(self._current_protocol_path())
        except Exception as exc:
            messagebox.showerror("Inspect Failed", str(exc))
            return None
        self._apply_summary(summary)
        self.log(f"[Opentrons] Loaded {summary.protocol_name}")
        return summary

    def run_file_now(self) -> None:
        try:
            path = self._current_protocol_path()
        except Exception as exc:
            messagebox.showerror("Run Failed", str(exc))
            return
        self._run_protocol_async(protocol_path=path, mode=self._mode_key(self._var_mode.get()))

    def add_file_to_queue(self) -> None:
        try:
            path = self._current_protocol_path()
            summary = self._runner.inspect_protocol(path)
            source_text = path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))
            return
        self._apply_summary(summary)
        mode = self._mode_key(self._var_mode.get())
        details = f"Opentrons {mode.upper()} {path.name}"
        if summary.has_pause:
            details += " [pause-aware]"
        self._queue_opentrons_item(
            details=details,
            mode=mode,
            protocol_name=summary.protocol_name,
            protocol_path=str(path),
            protocol_source=source_text,
            supports_pause=summary.has_pause,
            robot_host=self._robot_host(),
            robot_port=self._robot_port(),
        )

    def add_file_resume_to_queue(self) -> None:
        try:
            path = self._current_protocol_path()
            summary = self._runner.inspect_protocol(path)
            source_text = path.read_text(encoding="utf-8")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))
            return
        self._apply_summary(summary)
        self._queue_opentrons_resume_item(
            details=f"Opentrons RESUME {summary.protocol_name}",
            protocol_name=summary.protocol_name,
            resume_key=self._resume_key(protocol_name=summary.protocol_name, protocol_source=source_text),
        )

    def add_home_to_queue(self) -> None:
        try:
            robot_host = self._robot_host()
            robot_port = self._robot_port()
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))
            return
        if not robot_host:
            messagebox.showwarning("Missing Host", "Enter the OT-2 hostname or IP address first.")
            return
        self._queue_opentrons_home_item(
            details=f"Opentrons HOME {robot_host}",
            robot_host=robot_host,
            robot_port=robot_port,
        )

    def home_robot_now(self) -> None:
        try:
            robot_host = self._robot_host()
            robot_port = self._robot_port()
        except Exception as exc:
            messagebox.showerror("Home Failed", str(exc))
            return
        if not robot_host:
            messagebox.showwarning("Missing Host", "Enter the OT-2 hostname or IP address first.")
            return

        def _worker() -> None:
            ok = self._runner.home_robot(robot_host=robot_host, robot_port=robot_port)
            self._root.after(
                0,
                lambda: (
                    messagebox.showinfo("OT-2 Home", f"Home command sent to {robot_host}.")
                    if ok
                    else messagebox.showerror("OT-2 Home", f"Failed to home robot at {robot_host}.")
                ),
            )

        threading.Thread(target=_worker, daemon=True).start()

    def _run_protocol_async(
        self,
        *,
        mode: str,
        protocol_path: str | Path | None = None,
        protocol_source: str | None = None,
        protocol_name: str | None = None,
    ) -> None:
        robot_host = self._robot_host()
        try:
            robot_port = self._robot_port()
        except Exception as exc:
            messagebox.showerror("Run Failed", str(exc))
            return

        def _worker() -> None:
            data_folder = None
            session_mgr = getattr(self._session, "session_manager", None)
            if session_mgr is not None:
                data_folder = getattr(session_mgr, "current_experiment_path", None)
            ok, summary = self._runner.execute(
                protocol_path,
                source_text=protocol_source,
                protocol_name=protocol_name,
                mode=mode,
                data_folder=data_folder,
                robot_host=robot_host,
                robot_port=robot_port,
            )
            self._root.after(0, lambda: self._apply_summary(summary))
            self.log("[Opentrons] Run finished successfully." if ok else "[Opentrons] Run did not complete successfully.")

        threading.Thread(target=_worker, daemon=True).start()

    def _apply_summary(self, summary) -> None:
        warnings_text = ", ".join(summary.warnings) if summary.warnings else "None"
        self._summary_vars["name"].set(summary.protocol_name or "-")
        self._summary_vars["api"].set(summary.api_level or "-")
        self._summary_vars["robot"].set(summary.robot_type or "-")
        self._summary_vars["author"].set(summary.author or "-")
        self._summary_vars["description"].set(summary.description or "-")
        self._summary_vars["warnings"].set(warnings_text)

    def _queue_opentrons_item(
        self,
        *,
        details: str,
        mode: str,
        protocol_name: str,
        protocol_path: str | None = None,
        protocol_source: str | None = None,
        supports_pause: bool = False,
        robot_host: str | None = None,
        robot_port: int | None = None,
    ) -> None:
        resume_key = self._resume_key(
            protocol_name=protocol_name,
            protocol_path=protocol_path,
            protocol_source=protocol_source,
        )
        params = {
            "mode": mode,
            "protocol_name": protocol_name,
            "resume_key": resume_key,
            "supports_pause": bool(supports_pause),
        }
        if protocol_path:
            params["protocol_path"] = protocol_path
        if protocol_source:
            params["protocol_source"] = protocol_source
        if robot_host:
            params["robot_host"] = robot_host
        if robot_port is not None:
            params["robot_port"] = int(robot_port)
        item = {
            "type": "OPENTRONS_PROTOCOL",
            "status": "pending",
            "details": details,
            "opentrons_action": {
                "name": "PROTOCOL",
                "params": params,
            },
        }
        try:
            self._add_to_queue(item)
            self.log(f"[Queue] Added: {details}")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))

    def _queue_opentrons_resume_item(
        self,
        *,
        details: str,
        protocol_name: str,
        resume_key: str,
    ) -> None:
        item = {
            "type": "OPENTRONS_RESUME",
            "status": "pending",
            "details": details,
            "opentrons_action": {
                "name": "RESUME",
                "params": {
                    "protocol_name": protocol_name,
                    "resume_key": resume_key,
                },
            },
        }
        try:
            self._add_to_queue(item)
            self.log(f"[Queue] Added: {details}")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))

    def _queue_opentrons_home_item(
        self,
        *,
        details: str,
        robot_host: str,
        robot_port: int,
    ) -> None:
        item = {
            "type": "OPENTRONS_HOME",
            "status": "pending",
            "details": details,
            "opentrons_action": {
                "name": "HOME",
                "params": {
                    "robot_host": robot_host,
                    "robot_port": int(robot_port),
                },
            },
        }
        try:
            self._add_to_queue(item)
            self.log(f"[Queue] Added: {details}")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))

    @staticmethod
    def _resume_key(
        *,
        protocol_name: str,
        protocol_path: str | None = None,
        protocol_source: str | None = None,
    ) -> str:
        source = protocol_source
        if source is None and protocol_path:
            source = Path(protocol_path).read_text(encoding="utf-8")
        payload = f"{protocol_name}\n{source or ''}"
        return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]

    def _refresh_labware_tree(self) -> None:
        for row in self._labware_tree.get_children():
            self._labware_tree.delete(row)
        for idx, entry in enumerate(self._labware_rows):
            self._labware_tree.insert(
                "",
                "end",
                iid=str(idx),
                values=(entry["alias"], entry["load_name"], entry["slot"]),
            )

    def _refresh_step_tree(self) -> None:
        for row in self._step_tree.get_children():
            self._step_tree.delete(row)
        for idx, step in enumerate(self._step_rows, start=1):
            self._step_tree.insert(
                "",
                "end",
                iid=str(idx - 1),
                values=(idx, step.get("kind", ""), self._describe_step(step)),
            )

    def _selected_step_indices(self) -> list[int]:
        selected = []
        for item_id in self._step_tree.selection():
            try:
                selected.append(int(item_id))
            except (TypeError, ValueError):
                continue
        return sorted(selected)

    def _select_step_indices(self, indices: list[int]) -> None:
        valid = [str(idx) for idx in indices if 0 <= idx < len(self._step_rows)]
        self._step_tree.selection_set(valid)
        if valid:
            self._step_tree.focus(valid[0])
            self._step_tree.see(valid[0])
        else:
            self._step_tree.selection_remove(self._step_tree.selection())

    def _on_labware_selected(self, _event=None) -> None:
        sel = self._labware_tree.selection()
        if not sel:
            self._selected_labware_index = None
            return
        idx = int(sel[0])
        self._selected_labware_index = idx
        row = self._labware_rows[idx]
        self._labware_alias_var.set(row["alias"])
        self._labware_name_var.set(row["load_name"])
        self._labware_slot_var.set(row["slot"])

    def _upsert_labware(self) -> None:
        alias = (self._labware_alias_var.get() or "").strip()
        load_name = (self._labware_name_var.get() or "").strip()
        slot = (self._labware_slot_var.get() or "").strip()
        if not alias or not load_name or not slot:
            messagebox.showwarning("Missing Labware", "Alias, load name, and slot are required.")
            return
        warning = self._labware_name_warning(load_name)
        if warning:
            messagebox.showwarning("Check Labware Definition", warning)
        row = {"alias": alias, "load_name": load_name, "slot": slot}
        if self._selected_labware_index is None:
            self._labware_rows.append(row)
        else:
            self._labware_rows[self._selected_labware_index] = row
        self._selected_labware_index = None
        self._refresh_labware_name_options()
        self._refresh_labware_tree()
        self._apply_tracked_starting_tip(force=False)
        self.preview_builder_protocol()

    def _delete_labware(self) -> None:
        if self._selected_labware_index is None:
            return
        self._labware_rows.pop(self._selected_labware_index)
        self._selected_labware_index = None
        self._refresh_labware_name_options()
        self._refresh_labware_tree()
        self._apply_tracked_starting_tip(force=False)
        self.preview_builder_protocol()

    def _on_step_selected(self, _event=None) -> None:
        sel = self._step_tree.selection()
        if not sel:
            self._selected_step_index = None
            return
        idx = min(int(item) for item in sel)
        self._selected_step_index = idx
        self._set_step_warning("")
        step = self._step_rows[idx]
        self._step_kind_var.set(step.get("kind", "transfer"))
        self._step_volume_var.set(str(step.get("volume_ul", "")))
        self._step_source_alias_var.set(step.get("source_alias", ""))
        self._step_source_well_var.set(step.get("source_well", ""))
        self._step_dest_alias_var.set(step.get("dest_alias", ""))
        self._step_dest_well_var.set(step.get("dest_well", ""))
        self._step_location_var.set(step.get("location", "top"))
        self._step_new_tip_var.set(step.get("new_tip", "once"))
        self._step_pipette_key_var.set(step.get("pipette_key", "primary"))
        self._step_seconds_var.set(str(step.get("seconds", "")))
        self._step_comment_var.set(step.get("comment", step.get("message", "")))

    def _current_step_from_form(self) -> dict:
        kind = (self._step_kind_var.get() or "").strip().lower()
        step = {"kind": kind}
        if kind in {"transfer", "aspirate", "dispense"}:
            step["volume_ul"] = float(self._step_volume_var.get())
        if kind in {"transfer", "aspirate", "move_to", "blow_out", "pick_up_tip", "drop_tip"}:
            step["source_alias"] = (self._step_source_alias_var.get() or "").strip()
            step["source_well"] = (self._step_source_well_var.get() or "").strip().upper()
        if kind in {"transfer", "dispense"}:
            step["dest_alias"] = (self._step_dest_alias_var.get() or "").strip()
            step["dest_well"] = (self._step_dest_well_var.get() or "").strip().upper()
        if kind == "transfer":
            step["new_tip"] = (self._step_new_tip_var.get() or "once").strip().lower()
        if kind in {"transfer", "aspirate", "dispense", "move_to", "blow_out", "pick_up_tip", "drop_tip"}:
            step["pipette_key"] = (self._step_pipette_key_var.get() or "primary").strip().lower()
        if kind in {"transfer", "aspirate", "dispense", "move_to", "blow_out"}:
            step["location"] = (self._step_location_var.get() or "top").strip().lower()
        if kind == "delay":
            step["seconds"] = float(self._step_seconds_var.get())
        if kind == "comment":
            comment = (self._step_comment_var.get() or "").strip()
            if not comment:
                raise ValueError("Comment text is required for comment steps.")
            step["comment"] = comment
        if kind == "pause":
            message = (self._step_comment_var.get() or "").strip()
            if not message:
                raise ValueError("Pause message is required for pause steps.")
            step["message"] = message
        return step

    def _validated_step_from_form(self) -> dict:
        try:
            step = self._current_step_from_form()
        except Exception as exc:
            self._set_step_warning(str(exc))
            raise
        self._validate_step(step)
        self._set_step_warning("")
        return step

    def _add_step(self) -> None:
        try:
            step = self._validated_step_from_form()
        except Exception:
            return
        self._step_rows.append(step)
        self._selected_step_index = None
        self._refresh_step_tree()
        self._select_step_indices([len(self._step_rows) - 1])
        self.preview_builder_protocol()

    def _update_step(self) -> None:
        if self._selected_step_index is None:
            self._set_step_warning("Select a step first if you want to update it.")
            return
        try:
            step = self._validated_step_from_form()
        except Exception:
            return
        self._step_rows[self._selected_step_index] = step
        self._refresh_step_tree()
        self._select_step_indices([self._selected_step_index])
        self.preview_builder_protocol()

    def _delete_step(self) -> None:
        idxs = self._selected_step_indices()
        if not idxs:
            return
        for idx in reversed(idxs):
            self._step_rows.pop(idx)
        self._selected_step_index = None
        self._refresh_step_tree()
        next_idx = min(idxs[0], len(self._step_rows) - 1)
        if self._step_rows:
            self._select_step_indices([next_idx])
        self.preview_builder_protocol()

    def _clear_steps(self) -> None:
        self._step_rows.clear()
        self._selected_step_index = None
        self._refresh_step_tree()
        self.preview_builder_protocol()

    def _copy_selected_steps(self) -> None:
        idxs = self._selected_step_indices()
        if not idxs:
            messagebox.showwarning("No Selection", "Select one or more builder steps to copy.")
            return
        self._step_clipboard = [copy.deepcopy(self._step_rows[idx]) for idx in idxs]
        self.log(f"Copied {len(self._step_clipboard)} builder step(s).")

    def _paste_steps_after_selected(self) -> None:
        if not self._step_clipboard:
            messagebox.showwarning("Empty Clipboard", "Copy builder steps first.")
            return
        idxs = self._selected_step_indices()
        pos = (idxs[-1] + 1) if idxs else len(self._step_rows)
        pasted = [copy.deepcopy(step) for step in self._step_clipboard]
        self._step_rows[pos:pos] = pasted
        self._selected_step_index = None
        self._refresh_step_tree()
        self._select_step_indices(list(range(pos, pos + len(pasted))))
        self.preview_builder_protocol()
        self.log(f"Pasted {len(pasted)} builder step(s) after step {pos}.")

    def _duplicate_selected_steps(self) -> None:
        idxs = self._selected_step_indices()
        if not idxs:
            messagebox.showwarning("No Selection", "Select one or more builder steps to duplicate.")
            return
        duplicates = [copy.deepcopy(self._step_rows[idx]) for idx in idxs]
        insert_at = idxs[-1] + 1
        self._step_rows[insert_at:insert_at] = duplicates
        self._selected_step_index = None
        self._refresh_step_tree()
        self._select_step_indices(list(range(insert_at, insert_at + len(duplicates))))
        self.preview_builder_protocol()
        self.log(f"Duplicated {len(duplicates)} builder step(s).")

    def _show_step_ctx(self, event) -> None:
        row = self._step_tree.identify_row(event.y)
        if row:
            existing = self._step_tree.selection()
            if row not in existing:
                self._step_tree.selection_set(row)
        try:
            self._step_ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._step_ctx.grab_release()

    def _builder_spec(self) -> dict:
        return {
            "metadata": {
                "protocol_name": self._builder_protocol_name.get(),
                "author": self._builder_author.get(),
                "description": self._builder_description.get(),
                "api_level": self._builder_api.get(),
                "robot_type": self._builder_robot.get(),
            },
            "pipette": {
                "model": self._builder_pipette_model.get(),
                "mount": self._builder_mount.get(),
                "tiprack_alias": self._builder_tiprack_alias.get(),
                "starting_tip": self._builder_starting_tip.get(),
            },
            "secondary_pipette": (
                {
                    "model": self._builder_secondary_pipette_model.get(),
                    "mount": self._builder_secondary_mount.get(),
                    "tiprack_alias": self._builder_secondary_tiprack_alias.get(),
                    "starting_tip": self._builder_secondary_starting_tip.get(),
                }
                if bool(self._builder_use_dual_pipettes.get())
                else None
            ),
            "labware": list(self._labware_rows),
            "steps": list(self._step_rows),
        }

    def _render_preview(self, text: str) -> None:
        if self._preview_text is None:
            return
        self._preview_text.configure(state="normal")
        self._preview_text.delete("1.0", "end")
        self._preview_text.insert("1.0", text)
        self._preview_text.configure(state="disabled")

    def preview_builder_protocol(self):
        if not self._labware_rows and not self._step_rows:
            self._render_preview(
                "# Builder is empty\n"
                "# Add deck labware and protocol steps, or load a saved generated protocol into the builder.\n"
            )
            self._set_tip_budget_message("Tip budget: add a tiprack and preview the builder to estimate remaining tips.")
            self._apply_builder_summary_placeholder("Builder is empty.")
            return
        try:
            source, spec = generate_protocol_source(self._builder_spec())
            summary = self._runner.inspect_protocol(
                source_text=source,
                protocol_name=f"{spec['metadata']['protocol_name']}.py",
            )
            summary.warnings = self._merge_warning_lists(summary.warnings, self._builder_tip_usage_warnings(spec))
            self._apply_summary(summary)
            self._render_preview(source)
        except Exception as exc:
            self._set_tip_budget_message(f"Tip budget: unavailable ({exc})")
            self._apply_builder_summary_placeholder(str(exc))
            self._render_preview(f"# Builder error\n# {exc}\n")

    def _generate_builder_protocol(self) -> tuple[str, dict]:
        source, spec = generate_protocol_source(self._builder_spec())
        summary = self._runner.inspect_protocol(
            source_text=source,
            protocol_name=f"{spec['metadata']['protocol_name']}.py",
        )
        summary.warnings = self._merge_warning_lists(summary.warnings, self._builder_tip_usage_warnings(spec))
        self._apply_summary(summary)
        self._render_preview(source)
        return source, spec

    def run_builder_now(self) -> None:
        try:
            source, spec = self._generate_builder_protocol()
        except Exception as exc:
            messagebox.showerror("Build Failed", str(exc))
            return
        self.log(f"[Opentrons Builder] Running {summarize_protocol_spec(spec)}")
        self._run_protocol_async(
            mode=self._mode_key(self._builder_mode_var.get()),
            protocol_source=source,
            protocol_name=f"{spec['metadata']['protocol_name']}.py",
        )

    def add_builder_to_queue(self) -> None:
        try:
            source, spec = self._generate_builder_protocol()
        except Exception as exc:
            messagebox.showerror("Build Failed", str(exc))
            return
        mode = self._mode_key(self._builder_mode_var.get())
        protocol_name = spec["metadata"]["protocol_name"]
        details = f"Opentrons {mode.upper()} {protocol_name} (inline)"
        self._queue_opentrons_item(
            details=details,
            mode=mode,
            protocol_name=protocol_name,
            protocol_source=source,
            supports_pause=bool(getattr(self._runner.inspect_protocol(source_text=source, protocol_name=protocol_name), "has_pause", False)),
            robot_host=self._robot_host(),
            robot_port=self._robot_port(),
        )

    def add_builder_resume_to_queue(self) -> None:
        try:
            source, spec = self._generate_builder_protocol()
        except Exception as exc:
            messagebox.showerror("Build Failed", str(exc))
            return
        protocol_name = spec["metadata"]["protocol_name"]
        self._queue_opentrons_resume_item(
            details=f"Opentrons RESUME {protocol_name}",
            protocol_name=protocol_name,
            resume_key=self._resume_key(protocol_name=protocol_name, protocol_source=source),
        )

    def save_builder_to_library(self) -> None:
        try:
            source, spec = self._generate_builder_protocol()
        except Exception as exc:
            messagebox.showerror("Build Failed", str(exc))
            return
        params = spec_hash_params(spec)
        protocol_name = spec["metadata"]["protocol_name"]
        path, filename, created = self._session.opentrons_registry.save_protocol(
            kind="generated_ui_protocol",
            source=source,
            params=params,
            note=protocol_name,
        )
        self._load_protocol_files()
        self._var_path.set(str(path))
        tip_state = self._record_builder_tip_usage(spec, event_name="protocol_saved") if created else None
        if tip_state is not None and bool(self._builder_auto_tip_tracking.get()):
            next_tip = str(tip_state.get("next_tip") or "").strip().upper()
            if next_tip:
                self._builder_starting_tip.set(next_tip)
                self.preview_builder_protocol()
                self.log(f"[Opentrons Tip Tracker] Advanced next starting tip to {next_tip}.")
        elif not created:
            self.log("[Opentrons Tip Tracker] Existing saved protocol reused; tracked tip was not advanced.")
        self.log(f"[Opentrons Builder] Saved to library: {filename}")
        messagebox.showinfo("Saved", f"Protocol saved to library as:\n{filename}")

    @staticmethod
    def _describe_step(step: dict) -> str:
        kind = step.get("kind", "")
        if kind == "transfer":
            return (
                f"{step.get('volume_ul', 0):g} uL "
                f"{step.get('source_alias')}:{step.get('source_well')} -> "
                f"{step.get('dest_alias')}:{step.get('dest_well')} "
                f"(pipette={step.get('pipette_key', 'primary')}, new_tip={step.get('new_tip', 'once')}, location={step.get('location', 'top')})"
            )
        if kind == "move_to":
            return f"{step.get('source_alias')}:{step.get('source_well')} {step.get('location', 'top')} ({step.get('pipette_key', 'primary')})"
        if kind == "aspirate":
            return (
                f"{step.get('volume_ul', 0):g} uL from "
                f"{step.get('source_alias')}:{step.get('source_well')} "
                f"({step.get('pipette_key', 'primary')}, {step.get('location', 'top')})"
            )
        if kind == "dispense":
            return (
                f"{step.get('volume_ul', 0):g} uL to "
                f"{step.get('dest_alias')}:{step.get('dest_well')} "
                f"({step.get('pipette_key', 'primary')}, {step.get('location', 'top')})"
            )
        if kind == "blow_out":
            return f"at {step.get('source_alias')}:{step.get('source_well')} ({step.get('pipette_key', 'primary')}, {step.get('location', 'top')})"
        if kind in {"pick_up_tip", "drop_tip"}:
            suffix = f" {step.get('source_alias')}:{step.get('source_well')}" if step.get("source_well") else ""
            return f"{kind} {step.get('pipette_key', 'primary')}{suffix}".strip()
        if kind == "delay":
            return f"{step.get('seconds', 0):g} second(s)"
        if kind == "comment":
            return str(step.get("comment", "")).strip() or "(empty comment)"
        if kind == "pause":
            return f"pause: {str(step.get('message', '')).strip() or '(empty pause message)'}"
        if kind == "home":
            return "home robot"
        return str(step)

    def _robot_host(self) -> str:
        return (self._var_robot_host.get() or "").strip()

    def _robot_port(self) -> int:
        raw = (self._var_robot_port.get() or "").strip()
        if not raw:
            return int(OPENTRONS_DEFAULT_API_PORT)
        port = int(raw)
        if port <= 0 or port > 65535:
            raise ValueError(f"Invalid OT-2 API port: {raw}")
        return port
