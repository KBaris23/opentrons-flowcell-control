"""gui/tab_opentrons.py - Opentrons protocol browser and builder."""

from __future__ import annotations

import threading
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk

from config import OPENTRONS_DEFAULT_RUN_MODE, OPENTRONS_PROTOCOLS_DIR
from robot import OpentronsProtocolRunner, generate_protocol_source, summarize_protocol_spec
from robot.opentrons_builder import spec_hash_params


class OpentronsTab:
    """File-based and UI-native Opentrons protocol workflows."""

    _MODE_LABELS = {
        "validate": "Validate Only",
        "simulate": "Simulate (SDK required)",
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
        self._selected_labware_index: int | None = None
        self._selected_step_index: int | None = None

        self._build()
        self._seed_builder_defaults()
        self._load_protocol_files()
        self._refresh_labware_tree()
        self._refresh_step_tree()
        self.preview_builder_protocol()

    def _build(self) -> None:
        container = ttk.Frame(self._frame)
        container.pack(fill="both", expand=True, padx=8, pady=8)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(1, weight=1)
        container.rowconfigure(2, weight=1)

        self._summary_frame(container)

        notebook = ttk.Notebook(container)
        notebook.grid(row=1, column=0, sticky="nsew", pady=(8, 0))

        file_tab = ttk.Frame(notebook)
        builder_tab = ttk.Frame(notebook)
        notebook.add(file_tab, text="Protocol Files")
        notebook.add(builder_tab, text="Protocol Builder")

        self._build_file_tab(file_tab)
        self._build_builder_tab(builder_tab)

        log_frame = ttk.LabelFrame(container, text="Log")
        log_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log_text = tk.Text(log_frame, height=10, state="disabled")
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(4, 6), pady=6)
        self._log_text.configure(yscrollcommand=scroll.set)

    def _summary_frame(self, parent) -> None:
        summary = ttk.LabelFrame(parent, text="Protocol Summary")
        summary.grid(row=0, column=0, sticky="ew")
        summary.columnconfigure(1, weight=1)

        self._summary_vars = {
            "name": tk.StringVar(value="-"),
            "api": tk.StringVar(value="-"),
            "robot": tk.StringVar(value="-"),
            "author": tk.StringVar(value="-"),
            "description": tk.StringVar(value="-"),
            "warnings": tk.StringVar(value="-"),
        }
        rows = [
            ("Name:", "name"),
            ("API level:", "api"),
            ("Robot:", "robot"),
            ("Author:", "author"),
            ("Description:", "description"),
            ("Warnings:", "warnings"),
        ]
        for idx, (label, key) in enumerate(rows):
            ttk.Label(summary, text=label).grid(row=idx, column=0, padx=4, pady=2, sticky="ne")
            ttk.Label(
                summary,
                textvariable=self._summary_vars[key],
                wraplength=900,
                justify="left",
            ).grid(row=idx, column=1, padx=4, pady=2, sticky="w")

    def _build_file_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)

        top = ttk.LabelFrame(parent, text="Saved / Bundled Protocols")
        top.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
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
            values=[self._mode_label("validate"), self._mode_label("simulate")],
            state="readonly",
        )
        self._combo_mode.grid(row=2, column=1, padx=4, pady=4, sticky="w")

        sdk_text = "available" if self._runner.sdk_available else "not installed"
        sdk_color = "green" if self._runner.sdk_available else "#666"
        ttk.Label(top, text=f"Opentrons SDK: {sdk_text}", foreground=sdk_color).grid(
            row=2, column=2, padx=4, pady=4, sticky="w"
        )

        btns = ttk.Frame(top)
        btns.grid(row=3, column=0, columnspan=4, padx=4, pady=(6, 4), sticky="w")
        ttk.Button(btns, text="Inspect", command=self.inspect_current_file).pack(side="left", padx=4)
        ttk.Button(btns, text="Run Now", command=self.run_file_now).pack(side="left", padx=4)
        ttk.Button(btns, text="Add to Queue", command=self.add_file_to_queue).pack(side="left", padx=4)

    def _build_builder_tab(self, parent) -> None:
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        meta = ttk.LabelFrame(parent, text="Metadata")
        meta.grid(row=0, column=0, sticky="ew", padx=4, pady=4)
        for col in range(6):
            meta.columnconfigure(col, weight=1 if col in (1, 3, 5) else 0)

        self._builder_mode_var = tk.StringVar(value=self._mode_label(OPENTRONS_DEFAULT_RUN_MODE))
        self._builder_protocol_name = tk.StringVar(value="Generated Protocol")
        self._builder_author = tk.StringVar(value="Opentrons Flowcell Console")
        self._builder_description = tk.StringVar(value="Generated from the UI builder.")
        self._builder_api = tk.StringVar(value="2.19")
        self._builder_robot = tk.StringVar(value="OT-2")
        self._builder_pipette_model = tk.StringVar(value="p20_single_gen2")
        self._builder_mount = tk.StringVar(value="left")
        self._builder_tiprack_alias = tk.StringVar(value="tips")

        ttk.Label(meta, text="Name:").grid(row=0, column=0, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_protocol_name).grid(row=0, column=1, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="Author:").grid(row=0, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_author).grid(row=0, column=3, padx=4, pady=4, sticky="ew")
        ttk.Label(meta, text="Run mode:").grid(row=0, column=4, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_mode_var,
            values=[self._mode_label("validate"), self._mode_label("simulate")],
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

        ttk.Label(meta, text="Mount:").grid(row=3, column=0, padx=4, pady=4, sticky="e")
        ttk.Combobox(
            meta,
            textvariable=self._builder_mount,
            values=["left", "right"],
            state="readonly",
            width=10,
        ).grid(row=3, column=1, padx=4, pady=4, sticky="w")
        ttk.Label(meta, text="Tiprack alias:").grid(row=3, column=2, padx=4, pady=4, sticky="e")
        ttk.Entry(meta, textvariable=self._builder_tiprack_alias, width=12).grid(row=3, column=3, padx=4, pady=4, sticky="w")

        body = ttk.Panedwindow(parent, orient="horizontal")
        body.grid(row=1, column=0, sticky="nsew", padx=4, pady=4)

        left = ttk.Frame(body)
        right = ttk.Frame(body)
        for frame in (left, right):
            frame.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        right.rowconfigure(2, weight=1)
        body.add(left, weight=1)
        body.add(right, weight=2)

        self._build_labware_panel(left)
        self._build_steps_panel(right)

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
        ttk.Entry(form, textvariable=self._labware_name_var).grid(row=1, column=1, columnspan=3, padx=2, pady=2, sticky="ew")
        ttk.Label(form, text="Slot").grid(row=0, column=4, padx=2, pady=2, sticky="w")
        ttk.Entry(form, textvariable=self._labware_slot_var, width=8).grid(row=1, column=4, padx=2, pady=2, sticky="ew")

        btns = ttk.Frame(form)
        btns.grid(row=1, column=5, padx=2, pady=2, sticky="e")
        ttk.Button(btns, text="Add / Update", command=self._upsert_labware).pack(side="left", padx=2)
        ttk.Button(btns, text="Delete", command=self._delete_labware).pack(side="left", padx=2)

        cols = ("Alias", "Load Name", "Slot")
        self._labware_tree = ttk.Treeview(frame, columns=cols, show="headings", height=8)
        for col, width in (("Alias", 120), ("Load Name", 260), ("Slot", 60)):
            self._labware_tree.heading(col, text=col)
            self._labware_tree.column(col, width=width)
        self._labware_tree.grid(row=1, column=0, sticky="nsew", padx=4, pady=(0, 4))
        self._labware_tree.bind("<<TreeviewSelect>>", self._on_labware_selected)

    def _build_steps_panel(self, parent) -> None:
        controls = ttk.LabelFrame(parent, text="Step Builder")
        controls.grid(row=0, column=0, sticky="ew")
        for col in range(8):
            controls.columnconfigure(col, weight=1)

        self._step_kind_var = tk.StringVar(value="transfer")
        self._step_volume_var = tk.StringVar(value="1")
        self._step_source_alias_var = tk.StringVar(value="source")
        self._step_source_well_var = tk.StringVar(value="A1")
        self._step_dest_alias_var = tk.StringVar(value="dest")
        self._step_dest_well_var = tk.StringVar(value="A2")
        self._step_location_var = tk.StringVar(value="top")
        self._step_new_tip_var = tk.StringVar(value="once")
        self._step_seconds_var = tk.StringVar(value="1")

        fields = [
            ("Kind", self._step_kind_var, self._STEP_KINDS),
            ("Volume (uL)", self._step_volume_var, None),
            ("Source Alias", self._step_source_alias_var, None),
            ("Source Well", self._step_source_well_var, None),
            ("Dest Alias", self._step_dest_alias_var, None),
            ("Dest Well", self._step_dest_well_var, None),
            ("Location", self._step_location_var, ["top", "center", "bottom"]),
            ("New Tip", self._step_new_tip_var, ["once", "always", "never"]),
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

        step_btns = ttk.Frame(controls)
        step_btns.grid(row=3, column=1, columnspan=7, padx=2, pady=2, sticky="w")
        ttk.Button(step_btns, text="Add / Update Step", command=self._upsert_step).pack(side="left", padx=2)
        ttk.Button(step_btns, text="Delete Step", command=self._delete_step).pack(side="left", padx=2)
        ttk.Button(step_btns, text="Clear Steps", command=self._clear_steps).pack(side="left", padx=2)

        cols = ("#", "Kind", "Details")
        self._step_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)
        for col, width in (("#", 50), ("Kind", 120), ("Details", 520)):
            self._step_tree.heading(col, text=col)
            self._step_tree.column(col, width=width)
        self._step_tree.grid(row=1, column=0, sticky="nsew", pady=(6, 0))
        self._step_tree.bind("<<TreeviewSelect>>", self._on_step_selected)

        preview_frame = ttk.LabelFrame(parent, text="Generated Protocol Preview")
        preview_frame.grid(row=2, column=0, sticky="nsew", pady=(6, 0))
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.rowconfigure(0, weight=1)
        self._preview_text = tk.Text(preview_frame, height=14, state="disabled")
        self._preview_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        scroll = ttk.Scrollbar(preview_frame, orient="vertical", command=self._preview_text.yview)
        scroll.grid(row=0, column=1, sticky="ns", padx=(4, 6), pady=6)
        self._preview_text.configure(yscrollcommand=scroll.set)

        action_btns = ttk.Frame(parent)
        action_btns.grid(row=3, column=0, sticky="w", pady=(6, 0))
        ttk.Button(action_btns, text="Preview", command=self.preview_builder_protocol).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Run Now", command=self.run_builder_now).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Add to Queue", command=self.add_builder_to_queue).pack(side="left", padx=3)
        ttk.Button(action_btns, text="Save to Library", command=self.save_builder_to_library).pack(side="left", padx=3)

    def _seed_builder_defaults(self) -> None:
        self._labware_rows = [
            {"alias": "tips", "load_name": "opentrons_96_filtertiprack_20ul", "slot": "4"},
            {"alias": "source", "load_name": "opentrons_24_tuberack_nest_2ml_snapcap", "slot": "6"},
            {"alias": "dest", "load_name": "opentrons_24_tuberack_nest_2ml_snapcap", "slot": "7"},
        ]
        self._step_rows = [
            {
                "kind": "transfer",
                "volume_ul": 1.0,
                "source_alias": "source",
                "source_well": "A1",
                "dest_alias": "dest",
                "dest_well": "A2",
                "new_tip": "once",
            }
        ]

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
            try:
                label = str(path.relative_to(proto_dir))
            except ValueError:
                label = path.name
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
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))
            return
        self._apply_summary(summary)
        mode = self._mode_key(self._var_mode.get())
        details = f"Opentrons {mode.upper()} {path.name}"
        self._queue_opentrons_item(
            details=details,
            mode=mode,
            protocol_name=summary.protocol_name,
            protocol_path=str(path),
        )

    def _run_protocol_async(
        self,
        *,
        mode: str,
        protocol_path: str | Path | None = None,
        protocol_source: str | None = None,
        protocol_name: str | None = None,
    ) -> None:
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
    ) -> None:
        params = {
            "mode": mode,
            "protocol_name": protocol_name,
        }
        if protocol_path:
            params["protocol_path"] = protocol_path
        if protocol_source:
            params["protocol_source"] = protocol_source
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
        row = {"alias": alias, "load_name": load_name, "slot": slot}
        if self._selected_labware_index is None:
            self._labware_rows.append(row)
        else:
            self._labware_rows[self._selected_labware_index] = row
        self._selected_labware_index = None
        self._refresh_labware_tree()
        self.preview_builder_protocol()

    def _delete_labware(self) -> None:
        if self._selected_labware_index is None:
            return
        self._labware_rows.pop(self._selected_labware_index)
        self._selected_labware_index = None
        self._refresh_labware_tree()
        self.preview_builder_protocol()

    def _on_step_selected(self, _event=None) -> None:
        sel = self._step_tree.selection()
        if not sel:
            self._selected_step_index = None
            return
        idx = int(sel[0])
        self._selected_step_index = idx
        step = self._step_rows[idx]
        self._step_kind_var.set(step.get("kind", "transfer"))
        self._step_volume_var.set(str(step.get("volume_ul", "")))
        self._step_source_alias_var.set(step.get("source_alias", ""))
        self._step_source_well_var.set(step.get("source_well", ""))
        self._step_dest_alias_var.set(step.get("dest_alias", ""))
        self._step_dest_well_var.set(step.get("dest_well", ""))
        self._step_location_var.set(step.get("location", "top"))
        self._step_new_tip_var.set(step.get("new_tip", "once"))
        self._step_seconds_var.set(str(step.get("seconds", "")))

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
        if kind == "move_to":
            step["location"] = (self._step_location_var.get() or "top").strip().lower()
        if kind == "delay":
            step["seconds"] = float(self._step_seconds_var.get())
        return step

    def _upsert_step(self) -> None:
        try:
            step = self._current_step_from_form()
        except Exception as exc:
            messagebox.showerror("Invalid Step", str(exc))
            return
        if self._selected_step_index is None:
            self._step_rows.append(step)
        else:
            self._step_rows[self._selected_step_index] = step
        self._selected_step_index = None
        self._refresh_step_tree()
        self.preview_builder_protocol()

    def _delete_step(self) -> None:
        if self._selected_step_index is None:
            return
        self._step_rows.pop(self._selected_step_index)
        self._selected_step_index = None
        self._refresh_step_tree()
        self.preview_builder_protocol()

    def _clear_steps(self) -> None:
        self._step_rows.clear()
        self._selected_step_index = None
        self._refresh_step_tree()
        self.preview_builder_protocol()

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
            },
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
        try:
            source, spec = generate_protocol_source(self._builder_spec())
            summary = self._runner.inspect_protocol(
                source_text=source,
                protocol_name=f"{spec['metadata']['protocol_name']}.py",
            )
            self._apply_summary(summary)
            self._render_preview(source)
        except Exception as exc:
            self._render_preview(f"# Builder error\n# {exc}\n")

    def _generate_builder_protocol(self) -> tuple[str, dict]:
        source, spec = generate_protocol_source(self._builder_spec())
        summary = self._runner.inspect_protocol(
            source_text=source,
            protocol_name=f"{spec['metadata']['protocol_name']}.py",
        )
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
        )

    def save_builder_to_library(self) -> None:
        try:
            source, spec = self._generate_builder_protocol()
        except Exception as exc:
            messagebox.showerror("Build Failed", str(exc))
            return
        params = spec_hash_params(spec)
        protocol_name = spec["metadata"]["protocol_name"]
        path, filename = self._session.opentrons_registry.save_protocol(
            kind="generated_ui_protocol",
            source=source,
            params=params,
            note=protocol_name,
        )
        self._load_protocol_files()
        self._var_path.set(str(path))
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
                f"(new_tip={step.get('new_tip', 'once')})"
            )
        if kind == "move_to":
            return f"{step.get('source_alias')}:{step.get('source_well')} {step.get('location', 'top')}"
        if kind == "aspirate":
            return f"{step.get('volume_ul', 0):g} uL from {step.get('source_alias')}:{step.get('source_well')}"
        if kind == "dispense":
            return f"{step.get('volume_ul', 0):g} uL to {step.get('dest_alias')}:{step.get('dest_well')}"
        if kind == "blow_out":
            return f"at {step.get('source_alias')}:{step.get('source_well')}"
        if kind == "delay":
            return f"{step.get('seconds', 0):g} second(s)"
        if kind in {"pick_up_tip", "drop_tip"}:
            suffix = f" {step.get('source_alias')}:{step.get('source_well')}" if step.get("source_well") else ""
            return suffix.strip() or "default tip position"
        if kind == "home":
            return "home robot"
        return str(step)
