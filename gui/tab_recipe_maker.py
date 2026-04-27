"""
gui/tab_recipe_maker.py — Recipe Maker tab.

Provides a lightweight recipe builder that mirrors the Queue tab layout:
  - Recipe list (Treeview) with add/remove/reorder controls
  - Pump step editor (speed/volume/port/pause)
  - Method library browser (from methods/library_map.json) with search/filter

This tab does not execute; it only composes recipe items for later use.
"""

import copy
import json
import re
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk, simpledialog

from methods import library_map
from config import (
    BLOCKS_DIR,
    OPENTRONS_DEFAULT_API_PORT,
    OPENTRONS_DEFAULT_HOST,
    OPENTRONS_PROTOCOLS_DIR,
    COLLECTION_SYRINGE_CAPACITY_ML,
    FLOWCELL_FILL_VOLUME_UL,
    FLOWCELL_FILL_TARGET_S,
    SYRINGE_PRESETS_MM,
)
from core.pump_step_utils import (
    build_pump_details,
    default_collection_warn_ml,
    estimate_eta_seconds,
    rate_for_target_eta,
    volume_to_ul,
)
from core.opentrons_identity import resolve_protocol_id, resume_key_for_protocol
from robot import OpentronsProtocolRunner
from robot.opentrons_library_map import entry_for_path as opentrons_library_entry_for_path


class RecipeMakerTab:
    """Manages the 'Recipe Maker' notebook tab."""

    def __init__(self, parent_frame, on_send_to_queue=None):
        self._frame = parent_frame
        self._on_send_to_queue = on_send_to_queue
        self._recipe: list = []
        self._clipboard: list = []
        self._method_entries: dict = {}
        self._method_iid_to_key: dict = {}
        self._opentrons_runner = OpentronsProtocolRunner(log_callback=lambda _msg: None)
        self._opentrons_protocol_map: dict[str, Path] = {}
        self._last_selected = None
        self._style = ttk.Style(self._frame)
        self._repo_root = Path(__file__).resolve().parents[1]
        self._recipe_root = self._repo_root / "recipe_maker"
        self._default_blocks_dir = (self._repo_root / BLOCKS_DIR).resolve()
        self._custom_blocks_dir = (self._repo_root / "recipe_maker" / "custom_blocks").resolve()
        self._saved_blocks_dir = (self._repo_root / "recipe_maker" / "saved_recipes").resolve()
        self._recipe_root.mkdir(parents=True, exist_ok=True)
        self._default_blocks_dir.mkdir(parents=True, exist_ok=True)
        self._custom_blocks_dir.mkdir(parents=True, exist_ok=True)
        self._saved_blocks_dir.mkdir(parents=True, exist_ok=True)
        self._build()

    _COMPRESS_SEND_DEST_DIR = Path(r"Z:\opentrons(setup_4)")
    _TIP_WELL_RE = re.compile(r"^[A-Z]+[1-9][0-9]*$")

    # ── Build ──────────────────────────────────────────────────────────────

    def _build(self):
        pane = ttk.PanedWindow(self._frame, orient=tk.VERTICAL)
        pane.pack(fill="both", expand=True)

        top = ttk.Frame(pane); pane.add(top, weight=2)
        bottom = ttk.Frame(pane); pane.add(bottom, weight=1)

        # ── Control bar
        ctrl = ttk.Frame(top)
        ctrl.pack(pady=8, fill="x", padx=10)

        ttk.Button(ctrl, text="Add Pump Step",
                   command=self._add_pump_step).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Add Method Step",
                   command=self._add_method_step).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="Move Up",
                   command=lambda: self._move_selected(-1)).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Move Down",
                   command=lambda: self._move_selected(1)).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="Copy",
                   command=self._copy_selected).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Paste",
                   command=self._paste_after_selected).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Duplicate",
                   command=self._duplicate_selected).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Delete",
                   command=self._delete_selected).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="Save",
                   command=self._save_recipe).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Load",
                   command=self._load_recipe).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Validate",
                   command=self._show_recipe_validation).pack(side="left", padx=4)
        ttk.Button(ctrl, text="Clear",
                   command=self._clear_recipe).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="Send to Queue",
                   command=self._send_to_queue).pack(side="left", padx=4)
        self._lbl_collection_plan = ttk.Label(ctrl, text="Collection plan: 0 steps | 0.000 mL", foreground="#555")
        self._lbl_collection_plan.pack(side="right", padx=4)
        self._lbl_recipe_help = ttk.Label(
            top,
            text="Build recipes step by step. Use Validate before Send to Queue for a quick safety check.",
            foreground="#666",
        )
        self._lbl_recipe_help.pack(anchor="w", padx=10, pady=(0, 4))


        # ── Recipe Treeview
        cols = ("Type", "Block", "Details")
        self._style.configure("Recipe.Treeview", background="white", fieldbackground="white")
        self._style.map("Recipe.Treeview", background=[("selected", "#cce4ff")])
        tree_frame = ttk.Frame(top)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self._tree = ttk.Treeview(
            tree_frame,
            columns=cols,
            show="tree headings",
            height=10,
            style="Recipe.Treeview",
            selectmode="extended",
        )
        self._tree.heading("#0", text="#")
        self._tree.heading("Type", text="Type")
        self._tree.heading("Block", text="Block")
        self._tree.heading("Details", text="Details")
        self._tree.column("#0", width=50)
        self._tree.column("Type", width=160)
        self._tree.column("Block", width=180)
        self._tree.column("Details", width=420)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=tree_scroll.set)
        self._tree.tag_configure("volt", background="#dff5d8")
        self._tree.tag_configure("block", background="#fff3cd")
        self._tree.tag_configure("alert", background="#f8d7da")
        self._tree.tag_configure("default", background="#f2f2f2")

        legend = ttk.Frame(top)
        legend.pack(fill="x", padx=10, pady=(0, 6))
        ttk.Label(legend, text="Legend:").pack(side="left")
        self._legend_chip(legend, "#dff5d8", "Voltammetry (CV/SWV)")
        self._legend_chip(legend, "#fff3cd", "Block step")
        self._legend_chip(legend, "#f8d7da", "Alert/Pause")
        self._legend_chip(legend, "#f2f2f2", "Other")

        self._ctx = tk.Menu(self._tree, tearoff=0)
        self._ctx.add_command(label="Copy", command=self._copy_selected)
        self._ctx.add_command(label="Paste After", command=self._paste_after_selected)
        self._ctx.add_command(label="Duplicate", command=self._duplicate_selected)
        self._ctx.add_command(label="Select Range…", command=self._select_range_prompt)
        self._ctx.add_separator()
        self._ctx.add_command(label="Delete", command=self._delete_selected)
        self._tree.bind("<Button-3>", self._show_ctx)
        self._tree.bind("<Shift-Button-1>", self._select_range)
        self._tree.bind("<Double-1>", self._on_tree_double_click)
        self._tree.bind("<Control-c>", lambda e: self._copy_selected())
        self._tree.bind("<Control-v>", lambda e: self._paste_after_selected())
        self._tree.bind("<Control-d>", lambda e: self._duplicate_selected())

        # ── Bottom pane: editors / library
        bottom_nb = ttk.Notebook(bottom)
        bottom_nb.pack(fill="both", expand=True, padx=10, pady=8)

        pump_tab = ttk.Frame(bottom_nb)
        method_tab = ttk.Frame(bottom_nb)
        opentrons_tab = ttk.Frame(bottom_nb)
        block_tab = ttk.Frame(bottom_nb)
        bottom_nb.add(pump_tab, text="Pump Steps")
        bottom_nb.add(method_tab, text="Method Library")
        bottom_nb.add(opentrons_tab, text="Opentrons")
        bottom_nb.add(block_tab, text="Blocks")

        self._build_pump_editor(pump_tab)
        self._build_method_library(method_tab)
        self._build_opentrons_library(opentrons_tab)
        self._build_blocks_library(block_tab)

    def _legend_chip(self, parent, color: str, text: str):
        swatch = tk.Canvas(parent, width=12, height=12, highlightthickness=0)
        swatch.create_rectangle(0, 0, 12, 12, fill=color, outline="#777")
        swatch.pack(side="left", padx=(8, 2))
        ttk.Label(parent, text=text).pack(side="left", padx=(0, 6))

    @staticmethod
    def _raw_var_text(var) -> str:
        try:
            return str(var._tk.globalgetvar(var._name))
        except Exception:
            try:
                return str(var.get())
            except Exception:
                return ""

    @classmethod
    def _safe_float_var(cls, var, default: float = 0.0) -> float:
        raw = cls._raw_var_text(var).strip()
        if raw == "":
            return float(default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return float(default)

    @staticmethod
    def _syringe_capacity_ml_from_label(label: str, default: float = COLLECTION_SYRINGE_CAPACITY_ML) -> float:
        text = str(label or "").strip()
        if not text or text.lower() == "custom":
            return float(default)
        match = re.match(r"^\s*(\d+(?:\.\d+)?)(?:/\d+(?:\.\d+)?)?\s*mL\b", text, flags=re.IGNORECASE)
        if not match:
            return float(default)
        try:
            return float(match.group(1))
        except (TypeError, ValueError):
            return float(default)

    def _computed_pump_values(
        self,
        *,
        volume: float,
        units: str,
        target_eta_s: float,
        syringe_label: str,
    ) -> dict:
        capacity_ml = self._syringe_capacity_ml_from_label(syringe_label, COLLECTION_SYRINGE_CAPACITY_ML)
        warn_ml = default_collection_warn_ml(capacity_ml)
        rate = rate_for_target_eta(volume, units, target_eta_s)
        eta_s = estimate_eta_seconds(volume, rate or 0.0, units) if rate is not None else None
        return {
            "rate": rate,
            "eta_s": eta_s,
            "capacity_ml": capacity_ml,
            "warn_ml": warn_ml,
        }

    def _refresh_recipe_pump_computed(self):
        computed = self._computed_pump_values(
            volume=self._safe_float_var(self._pump_volume, FLOWCELL_FILL_VOLUME_UL),
            units=str(self._pump_units.get() or "uLmin"),
            target_eta_s=self._safe_float_var(self._pump_target_eta_s, FLOWCELL_FILL_TARGET_S),
            syringe_label=str(self._pump_syringe.get() or ""),
        )
        self._pump_collection_capacity_ml.set(float(computed["capacity_ml"]))
        self._pump_collection_warn_ml.set(float(computed["warn_ml"]))
        if computed["rate"] is None:
            self._pump_rate_text.set("Calculated rate: -")
        else:
            self._pump_rate.set(float(computed["rate"]))
            self._pump_rate_text.set(f"Calculated rate: {computed['rate']:.1f} {self._pump_units.get()}")
        self._update_recipe_pump_eta_label()

    # ── Pump editor ────────────────────────────────────────────────────────

    def _build_pump_editor(self, parent):
        pad = {"padx": 6, "pady": 4}
        from config import SYRINGE_PRESETS_MM

        ttk.Label(parent, text="Pump action:").grid(row=0, column=0, **pad, sticky="e")
        self._pump_action = tk.StringVar(value="HEXW2")
        ttk.Combobox(
            parent,
            textvariable=self._pump_action,
            values=[
                "HEXW2",
                "APPLY",
                "COMMAND",
                "START",
                "PAUSE",
                "STOP",
                "RESTART",
                "STATUS",
                "STATUS_PORT",
                "STATE_RESET",
                "WAIT",
                "ALERT",
            ],
            width=16,
            state="readonly",
        ).grid(row=0, column=1, **pad, sticky="w")

        ttk.Label(parent, text="Units:").grid(row=0, column=2, **pad, sticky="e")
        self._pump_units = tk.StringVar(value="uLmin")
        ttk.Combobox(
            parent,
            textvariable=self._pump_units,
            values=["mLmin", "mLhr", "uLmin", "uLhr"],
            width=10,
            state="readonly",
        ).grid(row=0, column=3, **pad, sticky="w")

        ttk.Label(parent, text="Mode:").grid(row=0, column=4, **pad, sticky="e")
        self._pump_mode = tk.StringVar(value="infuse")
        ttk.Combobox(
            parent,
            textvariable=self._pump_mode,
            values=["infuse", "withdraw"],
            width=10,
            state="readonly",
        ).grid(row=0, column=5, **pad, sticky="w")

        ttk.Label(parent, text="Diameter (mm) (syringe ID):").grid(row=0, column=6, **pad, sticky="e")
        self._pump_diameter_mm = tk.DoubleVar(value=11.73)
        ttk.Entry(parent, width=10, textvariable=self._pump_diameter_mm).grid(
            row=0, column=7, **pad, sticky="w"
        )

        ttk.Label(parent, text="Syringe preset:").grid(row=1, column=0, **pad, sticky="e")
        self._pump_syringe = tk.StringVar(value="5 mL (typical)")
        syringe_values = ["Custom"] + sorted(SYRINGE_PRESETS_MM.keys())
        syringe_combo = ttk.Combobox(
            parent,
            textvariable=self._pump_syringe,
            values=syringe_values,
            width=22,
            state="readonly",
        )
        syringe_combo.grid(row=1, column=1, columnspan=2, **pad, sticky="w")

        def _apply_preset(_e=None):
            key = (self._pump_syringe.get() or "").strip()
            if not key or key == "Custom":
                return
            mm = SYRINGE_PRESETS_MM.get(key)
            if mm is None:
                return
            try:
                self._pump_diameter_mm.set(float(mm))
            except Exception:
                pass
            self._refresh_recipe_pump_computed()

        syringe_combo.bind("<<ComboboxSelected>>", _apply_preset)
        ttk.Label(parent, text="Tip: Diameter = syringe inner diameter (ID).", foreground="#666").grid(
            row=1, column=3, columnspan=5, padx=6, pady=4, sticky="w"
        )

        ttk.Label(parent, text="Calculated rate:").grid(row=2, column=0, **pad, sticky="e")
        self._pump_rate = tk.DoubleVar(value=1.0)
        self._pump_rate_text = tk.StringVar(value="Calculated rate: -")
        ttk.Label(parent, textvariable=self._pump_rate_text, foreground="#555").grid(
            row=2, column=1, **pad, sticky="w"
        )

        ttk.Label(parent, text="Volume:").grid(row=2, column=2, **pad, sticky="e")
        self._pump_volume = tk.DoubleVar(value=25.0)
        ttk.Entry(parent, width=10, textvariable=self._pump_volume).grid(row=2, column=3, **pad, sticky="w")

        ttk.Label(parent, text="Delay (min):").grid(row=2, column=4, **pad, sticky="e")
        self._pump_delay_min = tk.DoubleVar(value=0.0)
        ttk.Entry(parent, width=10, textvariable=self._pump_delay_min).grid(row=2, column=5, **pad, sticky="w")

        ttk.Label(parent, text="Wait (sec):").grid(row=2, column=6, **pad, sticky="e")
        self._wait_seconds = tk.DoubleVar(value=11.0)
        ttk.Entry(parent, width=10, textvariable=self._wait_seconds).grid(row=2, column=7, **pad, sticky="w")

        ttk.Label(parent, text="Target ETA (s):").grid(row=3, column=0, **pad, sticky="e")
        self._pump_target_eta_s = tk.DoubleVar(value=FLOWCELL_FILL_TARGET_S)
        ttk.Entry(parent, width=10, textvariable=self._pump_target_eta_s).grid(row=3, column=1, **pad, sticky="w")
        ttk.Button(parent, text="Preset Flowcell Pull", command=self._apply_recipe_flowcell_pull_preset).grid(
            row=3, column=2, columnspan=2, **pad, sticky="w"
        )

        self._pump_track_collection = tk.BooleanVar(value=True)
        ttk.Checkbutton(parent, text="Track collected volume (Recommended)", variable=self._pump_track_collection).grid(
            row=3, column=5, columnspan=2, padx=6, pady=4, sticky="w"
        )

        ttk.Label(parent, text="Collection syringe:").grid(row=4, column=0, **pad, sticky="e")
        self._pump_collection_capacity_ml = tk.DoubleVar(value=COLLECTION_SYRINGE_CAPACITY_ML)
        self._pump_collection_text = tk.StringVar(value="Collection syringe: -")
        ttk.Label(parent, textvariable=self._pump_collection_text, foreground="#555").grid(
            row=4, column=1, columnspan=3, **pad, sticky="w"
        )

        self._pump_collection_warn_ml = tk.DoubleVar(value=default_collection_warn_ml(COLLECTION_SYRINGE_CAPACITY_ML))

        self._pump_eta_label = ttk.Label(parent, text="ETA: -", foreground="#555")
        self._pump_eta_label.grid(row=4, column=4, columnspan=4, padx=6, pady=4, sticky="w")

        ttk.Label(parent, text="Raw cmd:").grid(row=5, column=0, **pad, sticky="e")
        self._pump_raw_cmd = tk.StringVar(value="")
        ttk.Entry(parent, width=60, textvariable=self._pump_raw_cmd).grid(
            row=5, column=1, columnspan=7, **pad, sticky="w"
        )

        ttk.Label(parent, text="Alert message:").grid(row=6, column=0, **pad, sticky="e")
        self._alert_message = tk.StringVar(value="Check setup")
        ttk.Entry(parent, width=60, textvariable=self._alert_message).grid(
            row=6, column=1, columnspan=7, **pad, sticky="w"
        )

        ttk.Label(
            parent,
            text="Tip: Only relevant fields are used based on action type.",
            foreground="#666",
        ).grid(row=7, column=0, columnspan=8, padx=6, pady=(0, 6), sticky="w")

        ttk.Button(parent, text="Add Syringe State Reset", command=self._add_recipe_state_reset_step).grid(
            row=8, column=0, columnspan=2, padx=6, pady=(0, 6), sticky="w"
        )

        for var in (
            self._pump_units,
            self._pump_volume,
            self._pump_target_eta_s,
            self._pump_track_collection,
        ):
            try:
                var.trace_add("write", lambda *_: self._refresh_recipe_pump_computed())
            except Exception:
                pass
        _apply_preset()
        self._refresh_recipe_pump_computed()

    def _add_pump_step(self):
        action = self._pump_action.get().strip().upper()
        if not action:
            return
        try:
            computed = self._computed_pump_values(
                volume=self._safe_float_var(self._pump_volume, FLOWCELL_FILL_VOLUME_UL),
                units=str(self._pump_units.get() or "uLmin"),
                target_eta_s=self._safe_float_var(self._pump_target_eta_s, FLOWCELL_FILL_TARGET_S),
                syringe_label=str(self._pump_syringe.get() or ""),
            )
            item = self._build_pump_item(
                action=action,
                units=str(self._pump_units.get()),
                mode=str(self._pump_mode.get()),
                diameter_mm=self._safe_float_var(self._pump_diameter_mm, 11.73),
                rate=float(computed["rate"] or 0.0),
                volume=self._safe_float_var(self._pump_volume, FLOWCELL_FILL_VOLUME_UL),
                delay_min=self._safe_float_var(self._pump_delay_min, 0.0),
                cmd=(self._pump_raw_cmd.get() or "").strip(),
                wait=self._safe_float_var(self._wait_seconds, 11.0),
                alert=(self._alert_message.get() or "").strip(),
                target_eta_s=self._safe_float_var(self._pump_target_eta_s, FLOWCELL_FILL_TARGET_S),
                track_collection=bool(self._pump_track_collection.get()),
                collection_capacity_ml=float(computed["capacity_ml"]),
                collection_warn_ml=float(computed["warn_ml"]),
            )
        except Exception as exc:
            messagebox.showerror("Invalid pump step", str(exc))
            return
        self._recipe.append(item)
        self._refresh()

    def _apply_recipe_flowcell_pull_preset(self):
        self._pump_action.set("HEXW2")
        self._pump_units.set("uLmin")
        self._pump_mode.set("withdraw")
        self._pump_volume.set(float(FLOWCELL_FILL_VOLUME_UL))
        self._pump_target_eta_s.set(float(FLOWCELL_FILL_TARGET_S))
        self._pump_track_collection.set(True)
        try:
            self._pump_syringe.set("5 mL (typical)")
            self._pump_diameter_mm.set(float(SYRINGE_PRESETS_MM["5 mL (typical)"]))
        except Exception:
            pass
        self._refresh_recipe_pump_computed()

    def _update_recipe_pump_eta_label(self):
        units = str(self._pump_units.get() or "uLmin")
        volume = self._safe_float_var(self._pump_volume, 0.0)
        rate = self._safe_float_var(self._pump_rate, 0.0)
        eta_s = estimate_eta_seconds(
            volume,
            rate,
            units,
        )
        if eta_s is None:
            self._pump_eta_label.configure(text="ETA: -")
        else:
            extra = ""
            if bool(self._pump_track_collection.get()):
                volume_ul = volume_to_ul(volume, units)
                if volume_ul is not None:
                    extra = f" | collect {volume_ul / 1000.0:.3f} mL"
            self._pump_eta_label.configure(text=f"ETA: {eta_s:.1f}s{extra}")

        capacity_ml = self._safe_float_var(self._pump_collection_capacity_ml, COLLECTION_SYRINGE_CAPACITY_ML)
        warn_ml = self._safe_float_var(self._pump_collection_warn_ml, default_collection_warn_ml(capacity_ml))
        self._pump_collection_text.set(f"Collection syringe: {capacity_ml:g} mL | warning at {warn_ml:g} mL")

    # ── Method library ─────────────────────────────────────────────────────

    def _build_method_library(self, parent):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=6)

        ttk.Label(top, text="Search:").pack(side="left")
        self._method_search = tk.StringVar()
        self._method_search.trace_add("write", lambda *_: self._refresh_methods())
        ttk.Entry(top, textvariable=self._method_search, width=30).pack(side="left", padx=6)

        ttk.Label(top, text="Technique:").pack(side="left", padx=(10, 0))
        self._tech_filter = tk.StringVar(value="ALL")
        ttk.Combobox(
            top,
            textvariable=self._tech_filter,
            values=["ALL", "CV", "SWV"],
            state="readonly",
            width=8,
        ).pack(side="left", padx=6)
        self._tech_filter.trace_add("write", lambda *_: self._refresh_methods())

        ttk.Label(top, text="View:").pack(side="left", padx=(10, 0))
        self._mux_filter = tk.StringVar(value="ALL")
        ttk.Combobox(
            top,
            textvariable=self._mux_filter,
            values=["ALL", "BASE", "MUX"],
            state="readonly",
            width=8,
        ).pack(side="left", padx=6)
        self._mux_filter.trace_add("write", lambda *_: self._refresh_methods())

        ttk.Button(top, text="Refresh",
                   command=self._load_method_map).pack(side="left", padx=6)

        sweep = ttk.Frame(parent)
        sweep.pack(fill="x", padx=6, pady=(0, 4))

        ttk.Label(sweep, text="Sweep Start:").grid(row=0, column=0, **pad, sticky="e")
        self._sweep_start = tk.IntVar(value=1)
        ttk.Entry(sweep, width=6, textvariable=self._sweep_start).grid(row=0, column=1, **pad, sticky="w")

        ttk.Label(sweep, text="End:").grid(row=0, column=2, **pad, sticky="e")
        self._sweep_end = tk.IntVar(value=16)
        ttk.Entry(sweep, width=6, textvariable=self._sweep_end).grid(row=0, column=3, **pad, sticky="w")

        ttk.Label(sweep, text="Step:").grid(row=0, column=4, **pad, sticky="e")
        self._sweep_step = tk.IntVar(value=1)
        ttk.Entry(sweep, width=6, textvariable=self._sweep_step).grid(row=0, column=5, **pad, sticky="w")

        self._sweep_reverse = tk.BooleanVar(value=False)
        ttk.Checkbutton(sweep, text="Reverse", variable=self._sweep_reverse).grid(
            row=0, column=6, **pad, sticky="w"
        )

        ttk.Label(sweep, text="Repeats/ch:").grid(row=0, column=7, **pad, sticky="e")
        self._sweep_repeats = tk.IntVar(value=1)
        ttk.Entry(sweep, width=6, textvariable=self._sweep_repeats).grid(
            row=0, column=8, **pad, sticky="w"
        )

        ttk.Label(sweep, text="Custom order:").grid(row=1, column=0, **pad, sticky="e")
        self._sweep_custom = tk.StringVar(value="")
        ttk.Entry(sweep, width=44, textvariable=self._sweep_custom).grid(
            row=1, column=1, columnspan=5, **pad, sticky="we"
        )
        ttk.Label(sweep, text="e.g. 1,3,5,2,4").grid(row=1, column=6, columnspan=3, **pad, sticky="w")

        ttk.Button(
            sweep,
            text="Add Channel Sweep Block",
            command=self._add_method_sweep_block,
        ).grid(row=0, column=9, rowspan=2, padx=(12, 6), pady=4, sticky="ns")

        cols = ("Hash", "Note", "Technique", "Params")
        self._method_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)
        self._method_tree.heading("Hash", text="Hash")
        self._method_tree.heading("Note", text="Note")
        self._method_tree.heading("Technique", text="Technique")
        self._method_tree.heading("Params", text="Params")
        self._method_tree.column("Hash", width=140)
        self._method_tree.column("Note", width=220)
        self._method_tree.column("Technique", width=100)
        self._method_tree.column("Params", width=320)
        self._method_tree.pack(fill="both", expand=True, padx=6, pady=6)

        self._load_method_map()

        hint = ttk.Label(
            parent,
            text=(
                "Select a method and use Add Method Step, or configure channels and use "
                "'Add Channel Sweep Block'."
            ),
            foreground="#666",
        )
        hint.pack(side="bottom", anchor="w", padx=8, pady=(0, 6))

    def _build_opentrons_library(self, parent):
        pad = {"padx": 6, "pady": 4}

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=6)

        ttk.Label(top, text="Protocol:").pack(side="left")
        self._opentrons_protocol_var = tk.StringVar()
        self._opentrons_combo = ttk.Combobox(
            top,
            textvariable=self._opentrons_protocol_var,
            state="readonly",
            width=48,
        )
        self._opentrons_combo.pack(side="left", padx=6, fill="x", expand=True)
        self._opentrons_combo.bind("<<ComboboxSelected>>", self._on_opentrons_selected)
        ttk.Button(top, text="Refresh", command=self._load_opentrons_protocols).pack(side="left", padx=4)
        ttk.Button(top, text="Browse", command=self._browse_opentrons_protocol).pack(side="left", padx=4)

        form = ttk.Frame(parent)
        form.pack(fill="x", padx=6, pady=(0, 6))

        ttk.Label(form, text="Path:").grid(row=0, column=0, **pad, sticky="e")
        self._opentrons_path_var = tk.StringVar()
        ttk.Entry(form, textvariable=self._opentrons_path_var, width=80).grid(
            row=0, column=1, columnspan=4, **pad, sticky="ew"
        )
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Run mode:").grid(row=1, column=0, **pad, sticky="e")
        self._opentrons_mode_var = tk.StringVar(value="robot")
        ttk.Combobox(
            form,
            textvariable=self._opentrons_mode_var,
            values=["validate", "simulate", "robot"],
            state="readonly",
            width=12,
        ).grid(row=1, column=1, **pad, sticky="w")

        ttk.Label(form, text="Protocol name:").grid(row=1, column=2, **pad, sticky="e")
        self._opentrons_name_var = tk.StringVar(value="")
        ttk.Entry(form, textvariable=self._opentrons_name_var, width=30).grid(row=1, column=3, **pad, sticky="w")

        ttk.Label(form, text="Robot host/IP:").grid(row=2, column=0, **pad, sticky="e")
        self._opentrons_robot_host_var = tk.StringVar(value=str(OPENTRONS_DEFAULT_HOST))
        ttk.Entry(form, textvariable=self._opentrons_robot_host_var, width=24).grid(row=2, column=1, **pad, sticky="w")

        ttk.Label(form, text="Robot API port:").grid(row=2, column=2, **pad, sticky="e")
        self._opentrons_robot_port_var = tk.StringVar(value=str(int(OPENTRONS_DEFAULT_API_PORT)))
        ttk.Entry(form, textvariable=self._opentrons_robot_port_var, width=10).grid(row=2, column=3, **pad, sticky="w")

        self._opentrons_use_existing_tips_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            form,
            text="Use saved protocol starting tips (Recommended)",
            variable=self._opentrons_use_existing_tips_var,
            command=self._refresh_opentrons_tip_override_ui,
        ).grid(row=3, column=0, columnspan=2, **pad, sticky="w")

        self._opentrons_left_tip_label = ttk.Label(form, text="Left start tip:")
        self._opentrons_left_tip_label.grid(row=3, column=2, **pad, sticky="e")
        self._opentrons_left_tip_var = tk.StringVar(value="")
        self._opentrons_left_tip_entry = ttk.Entry(form, textvariable=self._opentrons_left_tip_var, width=10)
        self._opentrons_left_tip_entry.grid(row=3, column=3, **pad, sticky="w")

        self._opentrons_right_tip_label = ttk.Label(form, text="Right start tip:")
        self._opentrons_right_tip_label.grid(row=3, column=4, **pad, sticky="e")
        self._opentrons_right_tip_var = tk.StringVar(value="")
        self._opentrons_right_tip_entry = ttk.Entry(form, textvariable=self._opentrons_right_tip_var, width=10)
        self._opentrons_right_tip_entry.grid(row=3, column=5, **pad, sticky="w")

        self._opentrons_tip_override_hint_var = tk.StringVar(
            value="If unchecked, this recipe step will override left/right OT-2 starting tips and pause for confirmation before the run."
        )
        self._opentrons_tip_override_hint = ttk.Label(
            form,
            textvariable=self._opentrons_tip_override_hint_var,
            foreground="#666",
            wraplength=760,
            justify="left",
        )
        self._opentrons_tip_override_hint.grid(row=4, column=0, columnspan=6, padx=6, pady=(0, 4), sticky="w")

        self._opentrons_summary_var = tk.StringVar(value="Select an Opentrons protocol to see what will happen.")
        ttk.Label(
            form,
            textvariable=self._opentrons_summary_var,
            foreground="#1f4e79",
            wraplength=760,
            justify="left",
        ).grid(row=5, column=0, columnspan=6, padx=6, pady=(0, 4), sticky="w")

        btns = ttk.Frame(parent)
        btns.pack(fill="x", padx=6, pady=(0, 6))
        ttk.Button(btns, text="Add Protocol Step", command=self._add_opentrons_protocol_step).pack(side="left", padx=4)
        ttk.Button(btns, text="Add Resume Step", command=self._add_opentrons_resume_step).pack(side="left", padx=4)
        ttk.Button(btns, text="Add Home Step", command=self._add_opentrons_home_step).pack(side="left", padx=4)

        hint = ttk.Label(
            parent,
            text=(
                "Use this for existing pause-capable Opentrons scripts. Add the protocol step first, "
                "then place the matching resume step after the measurements/pump actions that should happen while OT-2 is paused."
            ),
            foreground="#666",
        )
        hint.pack(side="bottom", anchor="w", padx=8, pady=(0, 6))

        self._load_opentrons_protocols()
        self._refresh_opentrons_tip_override_ui()
        for var in (
            self._opentrons_mode_var,
            self._opentrons_name_var,
            self._opentrons_left_tip_var,
            self._opentrons_right_tip_var,
        ):
            try:
                var.trace_add("write", self._refresh_opentrons_summary)
            except Exception:
                pass
        try:
            self._opentrons_path_var.trace_add("write", self._on_opentrons_path_changed)
        except Exception:
            pass

    def _opentrons_protocol_label(self, path: Path, root: Path) -> str:
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = path.name
        try:
            summary = self._opentrons_runner.inspect_protocol(path)
            name = (summary.protocol_name or "").strip() or path.stem
        except Exception:
            name = path.stem
        return f"{name} | {rel}"

    def _load_opentrons_protocols(self):
        proto_dir = Path(OPENTRONS_PROTOCOLS_DIR)
        proto_dir.mkdir(parents=True, exist_ok=True)
        files = sorted(path.resolve() for path in proto_dir.rglob("*.py") if path.name != "__init__.py")
        self._opentrons_protocol_map.clear()
        labels = []
        for path in files:
            label = self._opentrons_protocol_label(path, proto_dir)
            labels.append(label)
            self._opentrons_protocol_map[label] = path
        self._opentrons_combo.configure(values=labels)
        if labels and self._opentrons_protocol_var.get() not in self._opentrons_protocol_map:
            self._opentrons_protocol_var.set(labels[0])
            self._on_opentrons_selected()

    def _on_opentrons_selected(self, _event=None):
        path = self._opentrons_protocol_map.get((self._opentrons_protocol_var.get() or "").strip())
        if path is None:
            return
        self._set_opentrons_path(path)

    def _browse_opentrons_protocol(self):
        path = filedialog.askopenfilename(
            title="Select Opentrons protocol",
            filetypes=(("Python files", "*.py"), ("All files", "*.*")),
            initialdir=str(Path(OPENTRONS_PROTOCOLS_DIR)),
        )
        if path:
            self._set_opentrons_path(Path(path).resolve())

    def _set_opentrons_path(self, path: Path):
        self._opentrons_path_var.set(str(path))
        try:
            summary = self._opentrons_runner.inspect_protocol(path)
            self._opentrons_name_var.set(summary.protocol_name or path.stem)
        except Exception:
            self._opentrons_name_var.set(path.stem)
        self._refresh_opentrons_tip_override_ui()
        self._refresh_opentrons_summary()

    def _current_opentrons_protocol(self) -> tuple[Path, str]:
        raw = (self._opentrons_path_var.get() or "").strip()
        if not raw:
            raise ValueError("Select an Opentrons protocol first.")
        path = Path(raw).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        path = path.resolve()
        if not path.exists():
            raise FileNotFoundError(f"Protocol file not found: {path}")
        protocol_name = (self._opentrons_name_var.get() or "").strip()
        if not protocol_name:
            try:
                protocol_name = self._opentrons_runner.inspect_protocol(path).protocol_name or path.stem
            except Exception:
                protocol_name = path.stem
        return path, protocol_name

    def _on_opentrons_path_changed(self, *_args):
        self._refresh_opentrons_tip_override_ui()
        self._refresh_opentrons_summary()

    @classmethod
    def _normalize_tip_well(cls, value) -> str:
        text = str(value or "").strip().upper()
        return text if cls._TIP_WELL_RE.fullmatch(text) else ""

    @staticmethod
    def _instrument_mounts(summary) -> tuple[str, ...] | None:
        if summary is None:
            return None
        mounts: list[str] = []
        for mount in getattr(summary, "instrument_mounts", ()) or ():
            text = str(mount or "").strip().lower()
            if text in {"left", "right"} and text not in mounts:
                mounts.append(text)
        return tuple(mounts)

    @classmethod
    def _tip_override_hint_text(cls, available_mounts: tuple[str, ...] | None) -> str:
        if available_mounts is None or set(available_mounts) == {"left", "right"}:
            return "If unchecked, this recipe step will override left/right OT-2 starting tips and pause for confirmation before the run."
        if not available_mounts:
            return "No left/right pipette loads were detected in this protocol, so starting tip overrides are unavailable."
        if available_mounts == ("left",):
            return "This protocol only loads a left pipette, so only the left starting tip can be overridden."
        if available_mounts == ("right",):
            return "This protocol only loads a right pipette, so only the right starting tip can be overridden."
        return "Only the pipette mounts loaded by this protocol can be given starting tip overrides."

    def _safe_opentrons_summary_for_path(self, raw_path: str, protocol_name: str = ""):
        path_text = str(raw_path or "").strip()
        if not path_text:
            return None
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        try:
            path = path.resolve()
        except Exception:
            pass
        try:
            return self._opentrons_runner.inspect_protocol(path, protocol_name=protocol_name or None)
        except Exception:
            return None

    def _sync_tip_override_widgets(
        self,
        *,
        use_existing: bool,
        summary,
        left_var,
        right_var,
        left_entry,
        right_entry,
        left_label=None,
        right_label=None,
        hint_var=None,
    ) -> tuple[str, ...] | None:
        available_mounts = self._instrument_mounts(summary)
        if available_mounts is not None:
            if "left" not in available_mounts and self._normalize_tip_well(self._raw_var_text(left_var)):
                left_var.set("")
            if "right" not in available_mounts and self._normalize_tip_well(self._raw_var_text(right_var)):
                right_var.set("")

        mount_states = {
            "left": available_mounts is None or "left" in available_mounts,
            "right": available_mounts is None or "right" in available_mounts,
        }
        label_widgets = {
            "left": left_label,
            "right": right_label,
        }
        entry_widgets = {
            "left": left_entry,
            "right": right_entry,
        }
        for mount in ("left", "right"):
            state = "disabled" if use_existing or not mount_states[mount] else "normal"
            try:
                entry_widgets[mount].configure(state=state)
            except Exception:
                pass
            label_widget = label_widgets[mount]
            if label_widget is not None:
                text = f"{mount.title()} start tip:"
                if available_mounts is not None and mount not in available_mounts:
                    text = f"{mount.title()} start tip (unused):"
                try:
                    label_widget.configure(text=text)
                except Exception:
                    pass
        if hint_var is not None:
            hint_var.set(self._tip_override_hint_text(available_mounts))
        return available_mounts

    def _refresh_opentrons_tip_override_ui(self):
        use_existing = bool(self._opentrons_use_existing_tips_var.get())
        summary = self._safe_opentrons_summary_for_path(
            self._opentrons_path_var.get(),
            protocol_name=self._opentrons_name_var.get(),
        )
        self._sync_tip_override_widgets(
            use_existing=use_existing,
            summary=summary,
            left_var=self._opentrons_left_tip_var,
            right_var=self._opentrons_right_tip_var,
            left_entry=self._opentrons_left_tip_entry,
            right_entry=self._opentrons_right_tip_entry,
            left_label=self._opentrons_left_tip_label,
            right_label=self._opentrons_right_tip_label,
            hint_var=self._opentrons_tip_override_hint_var,
        )
        self._refresh_opentrons_summary()

    def _tip_override_from_values(self, *, use_existing: bool, left_value, right_value, summary=None) -> dict | None:
        if use_existing:
            return None
        left_tip = self._normalize_tip_well(left_value)
        right_tip = self._normalize_tip_well(right_value)
        available_mounts = self._instrument_mounts(summary)
        if available_mounts is not None:
            if not available_mounts:
                raise ValueError("This protocol does not load a left or right pipette, so starting tip override is unavailable.")
            if left_tip and "left" not in available_mounts:
                raise ValueError("This protocol does not load a left pipette, so only the right starting tip can be overridden.")
            if right_tip and "right" not in available_mounts:
                raise ValueError("This protocol does not load a right pipette, so only the left starting tip can be overridden.")
        if not left_tip and not right_tip:
            if available_mounts == ("left",):
                raise ValueError("Enter a valid left OT-2 starting tip override, such as A1 or D4.")
            if available_mounts == ("right",):
                raise ValueError("Enter a valid right OT-2 starting tip override, such as A1 or D4.")
            raise ValueError("Enter at least one valid OT-2 starting tip override, such as A1 or D4.")
        return {
            "enabled": True,
            "left_starting_tip": left_tip,
            "right_starting_tip": right_tip,
            "require_confirmation": True,
        }

    def _refresh_opentrons_summary(self, *_args):
        path_text = (self._opentrons_path_var.get() or "").strip()
        if not path_text:
            self._opentrons_summary_var.set("Select an Opentrons protocol to see what will happen.")
            return
        path = Path(path_text)
        protocol_name = (self._opentrons_name_var.get() or path.stem).strip() or path.stem
        mode = (self._opentrons_mode_var.get() or "robot").strip().lower()
        try:
            summary = self._opentrons_runner.inspect_protocol(path)
            pause_aware = bool(summary.has_pause)
            available_mounts = self._instrument_mounts(summary)
        except Exception:
            pause_aware = False
            available_mounts = None
        parts = [f"This will queue OT-2 {mode} run for {protocol_name}."]
        if available_mounts == ("left",):
            parts.append("The protocol only loads a left pipette.")
        elif available_mounts == ("right",):
            parts.append("The protocol only loads a right pipette.")
        elif available_mounts == ("left", "right"):
            parts.append("The protocol loads both left and right pipettes.")
        elif available_mounts == ():
            parts.append("No left/right pipette loads were detected.")
        if bool(self._opentrons_use_existing_tips_var.get()):
            parts.append("It will keep the starting tips saved in the protocol.")
        else:
            left_tip = self._normalize_tip_well(self._opentrons_left_tip_var.get())
            right_tip = self._normalize_tip_well(self._opentrons_right_tip_var.get())
            tips = []
            if left_tip:
                tips.append(f"left={left_tip}")
            if right_tip:
                tips.append(f"right={right_tip}")
            if tips:
                parts.append("It will override starting tips to " + ", ".join(tips) + " and ask for confirmation before running.")
            elif available_mounts == ():
                parts.append("Starting tip overrides are unavailable for this protocol.")
            elif available_mounts == ("left",):
                parts.append("It will expect you to enter a left starting tip override before adding the step.")
            elif available_mounts == ("right",):
                parts.append("It will expect you to enter a right starting tip override before adding the step.")
            else:
                parts.append("It will expect you to enter at least one override tip before adding the step.")
        if pause_aware:
            parts.append("Because this protocol pauses, add as many resume steps as you need manually wherever they belong in the recipe.")
        else:
            parts.append("This protocol is not marked pause-aware.")
        self._opentrons_summary_var.set(" ".join(parts))

    def _current_tip_override(self, summary=None) -> dict | None:
        return self._tip_override_from_values(
            use_existing=bool(self._opentrons_use_existing_tips_var.get()),
            left_value=self._opentrons_left_tip_var.get(),
            right_value=self._opentrons_right_tip_var.get(),
            summary=summary,
        )

    @staticmethod
    def _tip_override_details(override: dict | None) -> str:
        if not override:
            return ""
        bits = []
        if override.get("left_starting_tip"):
            bits.append(f"L={override['left_starting_tip']}")
        if override.get("right_starting_tip"):
            bits.append(f"R={override['right_starting_tip']}")
        if not bits:
            return ""
        return " [tip override " + " ".join(bits) + "]"

    @classmethod
    def _opentrons_path_for_storage(cls, path: Path) -> str:
        resolved = Path(path).expanduser().resolve()
        try:
            return str(resolved.relative_to(cls._repo_root_for_helpers()))
        except ValueError:
            return str(resolved)

    @classmethod
    def _repo_root_for_helpers(cls) -> Path:
        return Path(__file__).resolve().parents[1]

    @classmethod
    def _resolve_recipe_opentrons_protocol_path(cls, protocol_path: str | Path) -> Path | None:
        raw = Path(protocol_path).expanduser()
        repo_root = cls._repo_root_for_helpers()
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(repo_root / raw)
            candidates.append(repo_root / OPENTRONS_PROTOCOLS_DIR / raw)
            candidates.append(repo_root / OPENTRONS_PROTOCOLS_DIR / raw.name)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if resolved.exists():
                return resolved
        proto_root = (repo_root / OPENTRONS_PROTOCOLS_DIR).resolve()
        if proto_root.exists():
            target_names = {raw.name.lower(), raw.stem.lower(), str(protocol_path).strip().lower()}
            for candidate in proto_root.rglob("*.py"):
                name = candidate.name.lower()
                stem = candidate.stem.lower()
                if name in target_names or stem in target_names:
                    try:
                        return candidate.resolve()
                    except Exception:
                        return candidate
        return None

    @classmethod
    def _opentrons_identity(cls, path: Path, protocol_name: str) -> tuple[str, str]:
        resolved_path = cls._resolve_recipe_opentrons_protocol_path(path) or Path(path).expanduser().resolve()
        found = opentrons_library_entry_for_path(resolved_path)
        if found is None:
            protocol_id = resolve_protocol_id(protocol_name=protocol_name, filename=resolved_path.name)
        else:
            key, entry = found
            protocol_id = resolve_protocol_id(
                protocol_id=entry.get("protocol_id"),
                protocol_name=protocol_name,
                filename=resolved_path.name,
                library_key=key,
            )
        return protocol_id, resume_key_for_protocol(protocol_id=protocol_id)

    def _current_opentrons_robot_target(self) -> tuple[str, int]:
        host = (self._opentrons_robot_host_var.get() or "").strip()
        if not host:
            raise ValueError("Enter the OT-2 host/IP first.")
        raw = (self._opentrons_robot_port_var.get() or str(int(OPENTRONS_DEFAULT_API_PORT))).strip()
        try:
            port = int(raw) if raw else int(OPENTRONS_DEFAULT_API_PORT)
        except ValueError as exc:
            raise ValueError(f"Invalid OT-2 API port: {raw}") from exc
        return host, port

    def _build_opentrons_protocol_item(
        self,
        *,
        path: Path,
        protocol_name: str,
        summary,
        mode: str,
        robot_host: str,
        robot_port: int,
        tip_override: dict | None,
    ) -> dict:
        protocol_id, resume_key = self._opentrons_identity(path, protocol_name)
        details = f"Opentrons {mode.upper()} {path.name}"
        if summary.has_pause:
            details += " [pause-aware]"
        details += self._tip_override_details(tip_override)
        return {
            "type": "OPENTRONS_PROTOCOL",
            "status": "pending",
            "details": details,
            "opentrons_action": {
                "name": "PROTOCOL",
                "params": {
                    "mode": mode,
                    "protocol_name": protocol_name,
                    "protocol_id": protocol_id,
                    "protocol_path": self._opentrons_path_for_storage(path),
                    "resume_key": resume_key,
                    "supports_pause": bool(summary.has_pause),
                    "robot_host": robot_host,
                    "robot_port": robot_port,
                    "tip_override": tip_override or {"enabled": False},
                },
            },
        }

    def _build_opentrons_resume_item(
        self,
        *,
        path: Path,
        protocol_name: str,
        summary,
    ) -> dict:
        if not bool(summary.has_pause):
            raise ValueError(f"{protocol_name} is not pause-aware, so it should not have a resume step.")
        protocol_id, resume_key = self._opentrons_identity(path, protocol_name)
        return {
            "type": "OPENTRONS_RESUME",
            "status": "pending",
            "details": f"Opentrons RESUME {protocol_name}",
            "opentrons_action": {
                "name": "RESUME",
                "params": {
                    "protocol_name": protocol_name,
                    "protocol_id": protocol_id,
                    "protocol_path": self._opentrons_path_for_storage(path),
                    "resume_key": resume_key,
                },
            },
        }

    @staticmethod
    def _build_opentrons_home_item(*, host: str, port: int) -> dict:
        return {
            "type": "OPENTRONS_HOME",
            "status": "pending",
            "details": f"Opentrons HOME {host}",
            "opentrons_action": {
                "name": "HOME",
                "params": {
                    "robot_host": host,
                    "robot_port": port,
                },
            },
        }

    def _add_opentrons_protocol_step(self):
        try:
            path, protocol_name = self._current_opentrons_protocol()
            summary = self._opentrons_runner.inspect_protocol(path)
            robot_host, robot_port = self._current_opentrons_robot_target()
            tip_override = self._current_tip_override(summary)
        except Exception as exc:
            messagebox.showerror("Invalid Protocol", str(exc))
            return
        mode = (self._opentrons_mode_var.get() or "robot").strip().lower()
        item = self._build_opentrons_protocol_item(
            path=path,
            protocol_name=protocol_name,
            summary=summary,
            mode=mode,
            robot_host=robot_host,
            robot_port=robot_port,
            tip_override=tip_override,
        )
        self._recipe.append(item)
        self._refresh()

    def _add_opentrons_resume_step(self):
        try:
            path, protocol_name = self._current_opentrons_protocol()
            summary = self._opentrons_runner.inspect_protocol(path)
        except Exception as exc:
            messagebox.showerror("Invalid Protocol", str(exc))
            return
        if not bool(summary.has_pause):
            messagebox.showwarning(
                "Resume Not Needed",
                f"{protocol_name} is not pause-aware, so it should not have a resume step.",
            )
            return
        item = self._build_opentrons_resume_item(
            path=path,
            protocol_name=protocol_name,
            summary=summary,
        )
        self._recipe.append(item)
        self._refresh()

    def _add_recipe_state_reset_step(self):
        self._recipe.append(
            {
                "type": "PUMP_STATE_RESET",
                "status": "pending",
                "details": build_pump_details("STATE_RESET", {}),
                "pump_action": {"name": "STATE_RESET", "params": {}},
            }
        )
        self._refresh()

    def _add_opentrons_home_step(self):
        try:
            host, port = self._current_opentrons_robot_target()
        except Exception as exc:
            messagebox.showerror("Invalid OT-2 Target", str(exc))
            return
        item = self._build_opentrons_home_item(host=host, port=port)
        self._recipe.append(item)
        self._refresh()

    def _load_method_map(self):
        self._method_entries = library_map.all_entries()
        self._refresh_methods()

    def _refresh_methods(self):
        for row in self._method_tree.get_children():
            self._method_tree.delete(row)
        self._method_iid_to_key.clear()

        search = (self._method_search.get() or "").strip().lower()
        tech = (self._tech_filter.get() or "ALL").upper()
        view = (getattr(self, "_mux_filter", tk.StringVar(value="ALL")).get() or "ALL").upper()

        for key, entry in sorted(self._method_entries.items()):
            technique = entry.get("technique", "")
            note = entry.get("note", "")
            mux_raw = entry.get("mux_channel")
            is_mux = mux_raw not in (None, "", 0, "0")
            if tech != "ALL" and technique.upper() != tech:
                continue
            if view == "BASE" and is_mux:
                continue
            if view == "MUX" and not is_mux:
                continue

            params = entry.get("params", {})
            params_str = ", ".join(f"{k}={v}" for k, v in params.items())
            hay = f"{key} {note} {technique} {params_str}".lower()
            if search and search not in hay:
                continue

            iid = self._method_tree.insert(
                "", "end",
                values=(key, note, technique, params_str),
            )
            self._method_iid_to_key[iid] = key

    def _selected_method_entry(self):
        sel = self._method_tree.selection()
        if not sel:
            return None
        key = self._method_iid_to_key.get(sel[0])
        if not key:
            return None
        entry = self._method_entries.get(key)
        if not entry:
            return None
        return key, entry

    def _add_method_step(self):
        selected = self._selected_method_entry()
        if not selected:
            messagebox.showwarning("No selection", "Select a method from the library list.")
            return
        key, entry = selected
        technique = entry.get("technique", "")
        params = entry.get("params", {})
        mux_channel = entry.get("mux_channel")

        details = f"{key}.ms"
        item = {
            "type": technique,
            "status": "pending",
            "details": details,
            "method_ref": {
                "hash_key": key,
                "technique": technique,
                "params": params,
                "mux_channel": mux_channel,
            },
        }
        self._recipe.append(item)
        self._refresh()

    def _parse_sweep_channels(self):
        custom = (self._sweep_custom.get() or "").strip()
        if custom:
            tokens = custom.replace(";", ",").split(",")
            channels = []
            for tok in tokens:
                t = tok.strip()
                if not t:
                    continue
                try:
                    ch = int(t)
                except ValueError:
                    raise ValueError(f"Invalid channel in custom order: '{t}'")
                if ch < 1 or ch > 16:
                    raise ValueError("Channel numbers must be between 1 and 16.")
                channels.append(ch)
            if not channels:
                raise ValueError("Custom order is empty.")
            return channels

        start = int(self._sweep_start.get())
        end = int(self._sweep_end.get())
        step = abs(int(self._sweep_step.get()))
        if step == 0:
            raise ValueError("Step must be >= 1.")
        if start < 1 or start > 16 or end < 1 or end > 16:
            raise ValueError("Sweep start/end must be between 1 and 16.")

        if start <= end:
            channels = list(range(start, end + 1, step))
        else:
            channels = list(range(start, end - 1, -step))
        if self._sweep_reverse.get():
            channels.reverse()
        if not channels:
            raise ValueError("Sweep channel list is empty.")
        return channels

    def _parse_sweep_repeats(self):
        repeats = int(self._sweep_repeats.get())
        if repeats < 1:
            raise ValueError("Repeats/ch must be >= 1.")
        if repeats > 1000:
            raise ValueError("Repeats/ch is too large (max 1000).")
        return repeats

    def _add_method_sweep_block(self):
        selected = self._selected_method_entry()
        if not selected:
            messagebox.showwarning("No selection", "Select a method from the library list.")
            return
        try:
            channels = self._parse_sweep_channels()
            repeats = self._parse_sweep_repeats()
        except Exception as exc:
            messagebox.showerror("Invalid sweep settings", str(exc))
            return

        key, entry = selected
        technique = entry.get("technique", "")
        params = copy.deepcopy(entry.get("params", {}))
        block_name = f"Sweep {technique} ({len(channels)} ch x {repeats})"
        for ch in channels:
            for rep in range(1, repeats + 1):
                rep_suffix = f" | rep {rep}/{repeats}" if repeats > 1 else ""
                item = {
                    "type": technique,
                    "status": "pending",
                    "details": f"{key}.ms | MUX ch {ch}{rep_suffix}",
                    "block_name": block_name,
                    "method_ref": {
                        "hash_key": key,
                        "technique": technique,
                        "params": copy.deepcopy(params),
                        "mux_channel": ch,
                    },
                }
                self._recipe.append(item)
        self._refresh()

    # ── Recipe list ops ────────────────────────────────────────────────────

    def _row_tag_for_item(self, item: dict) -> str:
        item_type = (item.get("type") or "").upper()
        if item_type in ("CV", "SWV"):
            return "volt"
        if item_type in ("PAUSE", "ALERT"):
            return "alert"
        if item.get("block_name") or item.get("block_ref"):
            return "block"
        return "default"

    def _refresh(self):
        self._apply_pump_speeds()
        for row in self._tree.get_children():
            self._tree.delete(row)
        for i, item in enumerate(self._recipe):
            tag = self._row_tag_for_item(item)
            self._tree.insert(
                "", "end", iid=str(i), text=str(i + 1),
                values=(
                    item.get("type", ""),
                    item.get("block_name", ""),
                    item.get("details", ""),
                ),
                tags=(tag,),
            )
        self._refresh_collection_summary()

    def _refresh_collection_summary(self):
        total_ul = 0.0
        steps = 0
        for item in self._recipe:
            item_type = str(item.get("type") or "").upper()
            if not item_type.startswith("PUMP_"):
                continue
            action = item.get("pump_action") or {}
            params = action.get("params") or {}
            if not bool(params.get("track_collection")):
                continue
            volume_ul = volume_to_ul(params.get("volume"), params.get("units", ""))
            if volume_ul is None:
                continue
            total_ul += max(0.0, volume_ul)
            steps += 1
        self._lbl_collection_plan.configure(
            text=f"Collection plan: {steps} steps | {total_ul / 1000.0:.3f} mL"
        )

    @staticmethod
    def _resume_key_style(resume_key: str) -> str:
        text = str(resume_key or "").strip()
        return "new" if text.startswith("resume_otproto_") else ("legacy" if text else "missing")

    @classmethod
    def _migrate_recipe_resume_keys(cls, items: list[dict]) -> tuple[list[dict], int]:
        migrated = [copy.deepcopy(item) for item in (items or [])]
        protocol_step_info: list[dict] = []
        for item in migrated:
            if str(item.get("type") or "").upper() != "OPENTRONS_PROTOCOL":
                continue
            action = item.get("opentrons_action") or {}
            params = dict(action.get("params") or {})
            protocol_name = str(params.get("protocol_name") or "").strip()
            protocol_path = str(params.get("protocol_path") or "").strip()
            if not protocol_name or not protocol_path:
                continue
            resolved_path = cls._resolve_recipe_opentrons_protocol_path(protocol_path)
            path = resolved_path or Path(protocol_path)
            stored_protocol_path = cls._opentrons_path_for_storage(path) if resolved_path is not None else protocol_path
            protocol_id, resume_key = cls._opentrons_identity(path, protocol_name)
            old_resume_key = str(params.get("resume_key") or "").strip()
            params["protocol_id"] = protocol_id
            params["resume_key"] = resume_key
            params["protocol_path"] = stored_protocol_path
            action["params"] = params
            item["opentrons_action"] = action
            protocol_step_info.append(
                {
                    "protocol_name": protocol_name,
                    "protocol_path": stored_protocol_path,
                    "old_resume_key": old_resume_key,
                    "new_resume_key": resume_key,
                    "protocol_id": protocol_id,
                }
            )

        changed = 0
        if protocol_step_info:
            by_old_key = {
                info["old_resume_key"]: info
                for info in protocol_step_info
                if info["old_resume_key"]
            }
            by_protocol_name: dict[str, list[dict]] = {}
            for info in protocol_step_info:
                by_protocol_name.setdefault(info["protocol_name"], []).append(info)

            for item in migrated:
                if str(item.get("type") or "").upper() != "OPENTRONS_RESUME":
                    continue
                action = item.get("opentrons_action") or {}
                params = dict(action.get("params") or {})
                old_resume_key = str(params.get("resume_key") or "").strip()
                protocol_name = str(params.get("protocol_name") or "").strip()
                match = by_old_key.get(old_resume_key)
                if match is None and len(by_protocol_name.get(protocol_name, [])) == 1:
                    match = by_protocol_name[protocol_name][0]
                if match is None:
                    continue
                if (
                    params.get("resume_key") != match["new_resume_key"]
                    or params.get("protocol_id") != match["protocol_id"]
                    or params.get("protocol_path") != match["protocol_path"]
                ):
                    params["resume_key"] = match["new_resume_key"]
                    params["protocol_id"] = match["protocol_id"]
                    params["protocol_path"] = match["protocol_path"]
                    action["params"] = params
                    item["opentrons_action"] = action
                    changed += 1

            for item, info in zip(
                [i for i in migrated if str(i.get("type") or "").upper() == "OPENTRONS_PROTOCOL"],
                protocol_step_info,
            ):
                params = ((item.get("opentrons_action") or {}).get("params") or {})
                if str(info["old_resume_key"] or "").strip() != str(info["new_resume_key"] or "").strip():
                    changed += 1
        return migrated, changed

    def _compact_recipe_item(self, item: dict) -> dict:
        compact = copy.deepcopy(item)
        if str(compact.get("type") or "").upper().startswith("OPENTRONS_"):
            action = compact.get("opentrons_action") or {}
            params = dict(action.get("params") or {})
            params.pop("protocol_source", None)
            action["params"] = params
            compact["opentrons_action"] = action
        return compact

    def _compact_recipe_items(self, items: list[dict] | None = None) -> list[dict]:
        return [self._compact_recipe_item(item) for item in (items or self._recipe)]

    def _validate_recipe(self) -> tuple[list[str], list[str]]:
        errors: list[str] = []
        warnings: list[str] = []
        paused_protocols: dict[str, int] = {}
        resumes_seen: dict[str, int] = {}

        for index, item in enumerate(self._recipe, start=1):
            item_type = str(item.get("type") or "").upper()
            if item_type.startswith("OPENTRONS_"):
                action = item.get("opentrons_action") or {}
                params = dict(action.get("params") or {})
                action_name = str(action.get("name") or "").strip().upper()
                protocol_name = str(params.get("protocol_name") or f"item {index}").strip()
                protocol_path = str(params.get("protocol_path") or "").strip()
                resume_key = str(params.get("resume_key") or "").strip()

                if action_name == "PROTOCOL":
                    resolved_protocol_path = None
                    if not protocol_path:
                        errors.append(f"Row {index}: Opentrons protocol step is missing a protocol path.")
                    else:
                        resolved_protocol_path = self._resolve_recipe_opentrons_protocol_path(protocol_path)
                        if resolved_protocol_path is None:
                            errors.append(f"Row {index}: Opentrons protocol file was not found: {protocol_path}")
                    if self._resume_key_style(resume_key) == "legacy":
                        warnings.append(f"Row {index}: {protocol_name} still uses a legacy resume key.")
                    if bool(params.get("supports_pause")):
                        paused_protocols[resume_key] = index
                    override = params.get("tip_override") or {}
                    if bool(override.get("enabled")):
                        left_tip = self._normalize_tip_well(override.get("left_starting_tip"))
                        right_tip = self._normalize_tip_well(override.get("right_starting_tip"))
                        if not left_tip and not right_tip:
                            errors.append(f"Row {index}: tip override is enabled but no valid left/right tip was set.")
                        elif resolved_protocol_path is not None:
                            try:
                                summary = self._opentrons_runner.inspect_protocol(resolved_protocol_path)
                            except Exception:
                                summary = None
                            available_mounts = self._instrument_mounts(summary)
                            if available_mounts is not None:
                                if left_tip and "left" not in available_mounts:
                                    errors.append(f"Row {index}: tip override sets left={left_tip}, but the protocol does not load a left pipette.")
                                if right_tip and "right" not in available_mounts:
                                    errors.append(f"Row {index}: tip override sets right={right_tip}, but the protocol does not load a right pipette.")

                elif action_name == "RESUME":
                    if not resume_key:
                        errors.append(f"Row {index}: Opentrons resume step is missing a resume key.")
                    resumes_seen[resume_key] = resumes_seen.get(resume_key, 0) + 1

            elif item_type == "PUMP_HEXW2":
                params = ((item.get("pump_action") or {}).get("params") or {})
                mode = str(params.get("mode") or "").strip().lower()
                if mode == "withdraw" and not bool(params.get("track_collection", False)):
                    warnings.append(f"Row {index}: HEXW2 withdraw does not track collected volume.")

        for resume_key, row in paused_protocols.items():
            if not resume_key:
                continue
            if resumes_seen.get(resume_key, 0) == 0:
                warnings.append(f"Row {row}: pause-aware Opentrons protocol has no matching resume step later in the recipe.")

        for index, item in enumerate(self._recipe, start=1):
            item_type = str(item.get("type") or "").upper()
            if item_type != "OPENTRONS_RESUME":
                continue
            params = ((item.get("opentrons_action") or {}).get("params") or {})
            resume_key = str(params.get("resume_key") or "").strip()
            if resume_key and resume_key not in paused_protocols:
                warnings.append(f"Row {index}: resume step does not match any pause-aware Opentrons protocol in this recipe.")

        return errors, warnings

    def _show_recipe_validation(self):
        errors, warnings = self._validate_recipe()
        if not errors and not warnings:
            messagebox.showinfo("Recipe Check", "No obvious recipe problems were found.")
            return
        lines = []
        if errors:
            lines.append("Errors:")
            lines.extend(f"- {msg}" for msg in errors)
        if warnings:
            if lines:
                lines.append("")
            lines.append("Warnings:")
            lines.extend(f"- {msg}" for msg in warnings)
        messagebox.showwarning("Recipe Check", "\n".join(lines))

    def _selected_indices(self):
        return sorted(
            self._tree.index(iid) for iid in self._tree.selection() if iid
        )

    def _select_range(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        if self._last_selected is None:
            self._tree.selection_set(row)
            self._last_selected = row
            return
        try:
            start = self._tree.index(self._last_selected)
            end = self._tree.index(row)
        except Exception:
            self._tree.selection_set(row)
            self._last_selected = row
            return
        if start > end:
            start, end = end, start
        self._tree.selection_set(self._tree.get_children()[start:end + 1])
        self._last_selected = row

    def _select_range_prompt(self):
        total = len(self._tree.get_children())
        if total == 0:
            return
        parent = self._tree.winfo_toplevel()
        start = simpledialog.askinteger(
            "Select Range",
            f"Start row (1-{total}):",
            minvalue=1, maxvalue=total,
            parent=parent,
        )
        if start is None:
            return
        end = simpledialog.askinteger(
            "Select Range",
            f"End row (1-{total}):",
            minvalue=1, maxvalue=total,
            parent=parent,
        )
        if end is None:
            return
        if start > end:
            start, end = end, start
        children = self._tree.get_children()
        self._tree.selection_set(children[start - 1:end])
        self._last_selected = children[end - 1]

    def _copy_selected(self):
        indices = self._selected_indices()
        if not indices:
            return
        self._clipboard = [copy.deepcopy(self._recipe[i]) for i in indices]

    def _paste_after_selected(self):
        if not self._clipboard:
            return
        indices = self._selected_indices()
        insert_at = (max(indices) + 1) if indices else len(self._recipe)
        for item in self._clipboard:
            self._recipe.insert(insert_at, copy.deepcopy(item))
            insert_at += 1
        self._refresh()

    def _duplicate_selected(self):
        indices = self._selected_indices()
        if not indices:
            return
        insert_at = max(indices) + 1
        for i in indices:
            self._recipe.insert(insert_at, copy.deepcopy(self._recipe[i]))
            insert_at += 1
        self._refresh()

    def _show_ctx(self, event):
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._last_selected = row
        self._ctx.tk_popup(event.x_root, event.y_root)

    def _delete_selected(self):
        indices = self._selected_indices()
        if not indices:
            return
        for i in reversed(indices):
            self._recipe.pop(i)
        self._refresh()

    def _move_selected(self, delta: int):
        indices = self._selected_indices()
        if not indices:
            return
        if delta < 0:
            for i in indices:
                if i <= 0:
                    continue
                self._recipe[i - 1], self._recipe[i] = self._recipe[i], self._recipe[i - 1]
        else:
            for i in reversed(indices):
                if i >= len(self._recipe) - 1:
                    continue
                self._recipe[i + 1], self._recipe[i] = self._recipe[i], self._recipe[i + 1]
        self._refresh()

    def _clear_recipe(self):
        if not self._recipe:
            return
        if not messagebox.askyesno("Clear recipe", "Remove all recipe items?"):
            return
        self._recipe.clear()
        self._refresh()

    def _send_to_queue(self):
        if not self._recipe:
            messagebox.showwarning("Empty", "No recipe items to send.")
            return
        if not callable(self._on_send_to_queue):
            messagebox.showwarning("Unavailable", "Queue is not available.")
            return
        errors, warnings = self._validate_recipe()
        if errors:
            messagebox.showerror("Recipe Has Errors", "\n".join(errors))
            return
        if warnings:
            proceed = messagebox.askyesno(
                "Recipe Warnings",
                "The recipe has warnings:\n\n"
                + "\n".join(f"- {msg}" for msg in warnings[:10])
                + ("\n\nContinue sending to queue anyway?" if len(warnings) <= 10 else "\n\nContinue sending to queue anyway?"),
            )
            if not proceed:
                return
        queued_items = []
        for item in self._compact_recipe_items():
            cloned = copy.deepcopy(item)
            cloned["status"] = "pending"
            queued_items.append(cloned)
        self._on_send_to_queue(queued_items)

    def _build_pump_item(
        self,
        *,
        action: str,
        units: str,
        mode: str,
        diameter_mm: float,
        rate: float,
        volume: float,
        delay_min: float,
        cmd: str,
        wait: float,
        alert: str,
        target_eta_s: float,
        track_collection: bool,
        collection_capacity_ml: float,
        collection_warn_ml: float,
    ) -> dict:
        action = (action or "").strip().upper()
        if not action:
            raise ValueError("Pump action is required.")

        if action == "STATE_RESET":
            return {
                "type": "PUMP_STATE_RESET",
                "status": "pending",
                "details": build_pump_details(action, {}),
                "pump_action": {"name": action, "params": {}},
            }
        if action == "WAIT":
            seconds = float(wait)
            return {
                "type": "PAUSE",
                "status": "pending",
                "details": f"Pause for {seconds:.1f} sec",
                "pause_seconds": seconds,
            }
        if action == "ALERT":
            msg = (alert or "").strip()
            if not msg:
                raise ValueError("Alert message cannot be empty.")
            return {
                "type": "ALERT",
                "status": "pending",
                "details": f"Alert: {msg}",
                "alert_message": msg,
            }

        pump_action = {"name": action, "params": {}}

        if action == "COMMAND":
            if not cmd:
                raise ValueError("Raw cmd cannot be empty for COMMAND action.")
            pump_action["params"] = {"cmd": cmd}

        elif action == "APPLY":
            if float(rate) <= 0:
                raise ValueError("Calculated pump rate is invalid. Check volume, units, and ETA.")
            pump_action["params"] = {
                "units": units,
                "mode": mode,
                "diameter_mm": float(diameter_mm),
                "rate": float(rate),
                "volume": float(volume),
            }

        elif action == "HEXW2":
            if float(rate) <= 0:
                raise ValueError("Calculated pump rate is invalid. Check volume, units, and ETA.")
            pump_action["params"] = {
                "units": units,
                "mode": mode,
                "diameter_mm": float(diameter_mm),
                "volume": float(volume),
                "rate": float(rate),
                "delay_min": float(delay_min),
                "start": True,
                "target_eta_s": float(target_eta_s),
                "track_collection": bool(track_collection),
                "collection_capacity_ml": float(collection_capacity_ml),
                "collection_warn_ml": float(collection_warn_ml),
            }

        elif action in {"START", "PAUSE", "STOP", "RESTART", "STATUS", "STATUS_PORT", "STATE_RESET"}:
            pump_action["params"] = {}

        else:
            raise ValueError(f"Unsupported pump action: {action}")

        return {
            "type": f"PUMP_{action}",
            "status": "pending",
            "details": build_pump_details(action, pump_action["params"]),
            "pump_action": pump_action,
        }

    def _apply_pump_speeds(self):
        # Backwards-compatible name retained; now just normalizes details for
        # Chemyx-style pump actions (and leaves unknowns untouched).
        for item in self._recipe:
            item_type = (item.get("type") or "").upper()
            if item_type == "PAUSE":
                try:
                    seconds = float(item.get("pause_seconds", 0.0))
                except (TypeError, ValueError):
                    seconds = 0.0
                item["details"] = f"Pause for {seconds:.1f} sec"
                continue
            if item_type == "ALERT":
                msg = str(item.get("alert_message") or "").strip()
                item["details"] = f"Alert: {msg}" if msg else "Alert"
                continue
            if not item_type.startswith("PUMP_"):
                continue

            action_info = item.get("pump_action") or {}
            name = str(action_info.get("name") or item_type.replace("PUMP_", "")).upper()
            params = action_info.get("params") or {}
            item["details"] = build_pump_details(name, params)

    @staticmethod
    def _preserve_recipe_item_metadata(source_item: dict, updated_item: dict) -> dict:
        for key in ("block_name", "block_ref"):
            if key in source_item and key not in updated_item:
                updated_item[key] = source_item[key]
        if "status" in source_item:
            updated_item["status"] = source_item.get("status")
        return updated_item

    def _on_tree_double_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        try:
            idx = self._tree.index(row)
        except Exception:
            return
        if idx < 0 or idx >= len(self._recipe):
            return
        item = self._recipe[idx]
        if self._is_pump_editable(item):
            self._edit_pump_step(idx)
            return
        if self._is_opentrons_editable(item):
            self._edit_opentrons_step(idx)

    def _is_opentrons_editable(self, item: dict) -> bool:
        return (item.get("type") or "").upper() in {"OPENTRONS_PROTOCOL", "OPENTRONS_RESUME", "OPENTRONS_HOME"}

    def _linked_protocol_path_for_resume(self, index: int, item: dict) -> str:
        params = ((item.get("opentrons_action") or {}).get("params") or {})
        direct_path = str(params.get("protocol_path") or "").strip()
        if direct_path:
            return direct_path
        target_resume_key = str(params.get("resume_key") or "").strip()
        target_protocol_name = str(params.get("protocol_name") or "").strip()
        for candidate_index in range(index - 1, -1, -1):
            candidate = self._recipe[candidate_index]
            if str(candidate.get("type") or "").upper() != "OPENTRONS_PROTOCOL":
                continue
            candidate_params = ((candidate.get("opentrons_action") or {}).get("params") or {})
            candidate_path = str(candidate_params.get("protocol_path") or "").strip()
            if not candidate_path:
                continue
            if target_resume_key and str(candidate_params.get("resume_key") or "").strip() == target_resume_key:
                return candidate_path
            if (
                not target_resume_key
                and target_protocol_name
                and str(candidate_params.get("protocol_name") or "").strip() == target_protocol_name
            ):
                return candidate_path
        return ""

    def _extract_opentrons_fields(self, index: int, item: dict) -> dict:
        item_type = str(item.get("type") or "").upper()
        action = item.get("opentrons_action") or {}
        params = dict(action.get("params") or {})
        override = dict(params.get("tip_override") or {})
        path = str(params.get("protocol_path") or "").strip()
        if item_type == "OPENTRONS_RESUME" and not path:
            path = self._linked_protocol_path_for_resume(index, item)
        protocol_name = str(params.get("protocol_name") or "").strip()
        if not protocol_name and path:
            protocol_name = Path(path).stem
        return {
            "type": item_type,
            "path": path,
            "protocol_name": protocol_name,
            "mode": str(params.get("mode") or "robot").strip().lower() or "robot",
            "robot_host": str(params.get("robot_host") or OPENTRONS_DEFAULT_HOST).strip(),
            "robot_port": str(params.get("robot_port") or int(OPENTRONS_DEFAULT_API_PORT)).strip(),
            "use_existing_tips": not bool(override.get("enabled")),
            "left_tip": self._normalize_tip_well(override.get("left_starting_tip")),
            "right_tip": self._normalize_tip_well(override.get("right_starting_tip")),
        }

    def _edit_opentrons_step(self, index: int):
        item = self._recipe[index]
        fields = self._extract_opentrons_fields(index, item)
        item_type = fields["type"]

        win = tk.Toplevel(self._frame)
        title_map = {
            "OPENTRONS_PROTOCOL": "Edit Opentrons Step",
            "OPENTRONS_RESUME": "Edit Opentrons Resume",
            "OPENTRONS_HOME": "Edit Opentrons Home",
        }
        win.title(title_map.get(item_type, "Edit Opentrons Step"))
        win.transient(self._frame.winfo_toplevel())
        win.grab_set()
        win.columnconfigure(1, weight=1)

        pad = {"padx": 6, "pady": 4}

        def _resolve_protocol_fields(path_text: str, protocol_name_text: str):
            raw = str(path_text or "").strip()
            if not raw:
                raise ValueError("Select an Opentrons protocol first.")
            path = self._resolve_recipe_opentrons_protocol_path(raw)
            if path is None:
                raise FileNotFoundError(f"Protocol file not found: {raw}")
            summary = self._opentrons_runner.inspect_protocol(path)
            protocol_name = str(protocol_name_text or "").strip() or summary.protocol_name or path.stem
            return path, summary, protocol_name

        def _parse_robot_target(host_text: str, port_text: str) -> tuple[str, int]:
            host = str(host_text or "").strip()
            if not host:
                raise ValueError("Enter the OT-2 host/IP first.")
            raw_port = str(port_text or str(int(OPENTRONS_DEFAULT_API_PORT))).strip()
            try:
                port = int(raw_port) if raw_port else int(OPENTRONS_DEFAULT_API_PORT)
            except ValueError as exc:
                raise ValueError(f"Invalid OT-2 API port: {raw_port}") from exc
            return host, port

        if item_type in {"OPENTRONS_PROTOCOL", "OPENTRONS_RESUME"}:
            path_var = tk.StringVar(value=fields["path"])
            name_var = tk.StringVar(value=fields["protocol_name"])
            summary_var = tk.StringVar(value="")
            summary_cache = {"value": None}

            ttk.Label(win, text="Path:").grid(row=0, column=0, **pad, sticky="e")
            ttk.Entry(win, textvariable=path_var, width=72).grid(row=0, column=1, columnspan=5, **pad, sticky="ew")

            def _browse_protocol():
                selected = filedialog.askopenfilename(
                    title="Select Opentrons protocol",
                    filetypes=(("Python files", "*.py"), ("All files", "*.*")),
                    initialdir=str(Path(OPENTRONS_PROTOCOLS_DIR)),
                )
                if not selected:
                    return
                resolved = Path(selected).resolve()
                path_var.set(str(resolved))
                try:
                    summary = self._opentrons_runner.inspect_protocol(resolved)
                    name_var.set(summary.protocol_name or resolved.stem)
                except Exception:
                    name_var.set(resolved.stem)

            ttk.Button(win, text="Browse", command=_browse_protocol).grid(row=0, column=6, **pad, sticky="w")

            ttk.Label(win, text="Protocol name:").grid(row=1, column=0, **pad, sticky="e")
            ttk.Entry(win, textvariable=name_var, width=32).grid(row=1, column=1, **pad, sticky="w")

            next_row = 2
            if item_type == "OPENTRONS_PROTOCOL":
                ttk.Label(win, text="Run mode:").grid(row=1, column=2, **pad, sticky="e")
                mode_var = tk.StringVar(value=fields["mode"])
                ttk.Combobox(
                    win,
                    textvariable=mode_var,
                    values=["validate", "simulate", "robot"],
                    width=12,
                    state="readonly",
                ).grid(row=1, column=3, **pad, sticky="w")

                ttk.Label(win, text="Robot host/IP:").grid(row=2, column=0, **pad, sticky="e")
                host_var = tk.StringVar(value=fields["robot_host"])
                ttk.Entry(win, textvariable=host_var, width=24).grid(row=2, column=1, **pad, sticky="w")

                ttk.Label(win, text="Robot API port:").grid(row=2, column=2, **pad, sticky="e")
                port_var = tk.StringVar(value=fields["robot_port"])
                ttk.Entry(win, textvariable=port_var, width=10).grid(row=2, column=3, **pad, sticky="w")

                use_existing_var = tk.BooleanVar(value=bool(fields["use_existing_tips"]))
                ttk.Checkbutton(
                    win,
                    text="Use saved protocol starting tips (Recommended)",
                    variable=use_existing_var,
                ).grid(row=3, column=0, columnspan=2, **pad, sticky="w")

                left_label = ttk.Label(win, text="Left start tip:")
                left_label.grid(row=3, column=2, **pad, sticky="e")
                left_var = tk.StringVar(value=fields["left_tip"])
                left_entry = ttk.Entry(win, textvariable=left_var, width=10)
                left_entry.grid(row=3, column=3, **pad, sticky="w")

                right_label = ttk.Label(win, text="Right start tip:")
                right_label.grid(row=3, column=4, **pad, sticky="e")
                right_var = tk.StringVar(value=fields["right_tip"])
                right_entry = ttk.Entry(win, textvariable=right_var, width=10)
                right_entry.grid(row=3, column=5, **pad, sticky="w")

                hint_var = tk.StringVar(value="")
                ttk.Label(
                    win,
                    textvariable=hint_var,
                    foreground="#666",
                    wraplength=760,
                    justify="left",
                ).grid(row=4, column=0, columnspan=7, padx=6, pady=(0, 4), sticky="w")
                summary_row = 5
                next_row = 6
            else:
                summary_row = 2
                next_row = 3

            def _refresh_popup_summary(*_args):
                summary = self._safe_opentrons_summary_for_path(path_var.get(), protocol_name=name_var.get())
                summary_cache["value"] = summary
                protocol_display = (name_var.get() or Path((path_var.get() or "").strip() or "protocol.py").stem).strip() or "selected protocol"
                if item_type == "OPENTRONS_PROTOCOL":
                    available_mounts = self._sync_tip_override_widgets(
                        use_existing=bool(use_existing_var.get()),
                        summary=summary,
                        left_var=left_var,
                        right_var=right_var,
                        left_entry=left_entry,
                        right_entry=right_entry,
                        left_label=left_label,
                        right_label=right_label,
                        hint_var=hint_var,
                    )
                    parts = [f"This row will run {protocol_display} in {(mode_var.get() or 'robot').strip().lower()} mode."]
                    if available_mounts == ("left",):
                        parts.append("The protocol only loads a left pipette.")
                    elif available_mounts == ("right",):
                        parts.append("The protocol only loads a right pipette.")
                    elif available_mounts == ("left", "right"):
                        parts.append("The protocol loads both left and right pipettes.")
                    elif available_mounts == ():
                        parts.append("No left/right pipette loads were detected.")
                    if bool(use_existing_var.get()):
                        parts.append("It will keep the protocol's saved starting tips.")
                    else:
                        left_tip = self._normalize_tip_well(left_var.get())
                        right_tip = self._normalize_tip_well(right_var.get())
                        tips = []
                        if left_tip:
                            tips.append(f"left={left_tip}")
                        if right_tip:
                            tips.append(f"right={right_tip}")
                        if tips:
                            parts.append("It will override starting tips to " + ", ".join(tips) + ".")
                        elif available_mounts == ("left",):
                            parts.append("Enter a left starting tip override before saving.")
                        elif available_mounts == ("right",):
                            parts.append("Enter a right starting tip override before saving.")
                        elif available_mounts == ():
                            parts.append("Starting tip overrides are unavailable for this protocol.")
                        else:
                            parts.append("Enter at least one starting tip override before saving.")
                    if summary is not None and bool(summary.has_pause):
                        parts.append("This protocol is pause-aware.")
                    elif summary is not None:
                        parts.append("This protocol is not marked pause-aware.")
                    summary_var.set(" ".join(parts))
                    return

                if not (path_var.get() or "").strip():
                    summary_var.set("Select the pause-aware protocol this resume step should target.")
                    return
                if summary is None:
                    summary_var.set(f"This resume step will target {protocol_display} once the file can be loaded.")
                elif summary.has_pause:
                    summary_var.set(f"This resume step will use the resume key derived from {protocol_display}.")
                else:
                    summary_var.set(f"{protocol_display} is not marked pause-aware, so it should not have a resume step.")

            ttk.Label(
                win,
                textvariable=summary_var,
                foreground="#1f4e79",
                wraplength=760,
                justify="left",
            ).grid(row=summary_row, column=0, columnspan=7, padx=6, pady=(0, 4), sticky="w")

            watch_vars = [path_var, name_var]
            if item_type == "OPENTRONS_PROTOCOL":
                watch_vars.extend([mode_var, left_var, right_var, use_existing_var])
            for watched_var in watch_vars:
                try:
                    watched_var.trace_add("write", _refresh_popup_summary)
                except Exception:
                    pass
            _refresh_popup_summary()

            btns = ttk.Frame(win)
            btns.grid(row=next_row, column=0, columnspan=7, pady=(6, 8))

            def _apply():
                try:
                    path, summary, protocol_name = _resolve_protocol_fields(path_var.get(), name_var.get())
                    if item_type == "OPENTRONS_PROTOCOL":
                        host, port = _parse_robot_target(host_var.get(), port_var.get())
                        tip_override = self._tip_override_from_values(
                            use_existing=bool(use_existing_var.get()),
                            left_value=left_var.get(),
                            right_value=right_var.get(),
                            summary=summary,
                        )
                        new_item = self._build_opentrons_protocol_item(
                            path=path,
                            protocol_name=protocol_name,
                            summary=summary,
                            mode=(mode_var.get() or "robot").strip().lower(),
                            robot_host=host,
                            robot_port=port,
                            tip_override=tip_override,
                        )
                    else:
                        new_item = self._build_opentrons_resume_item(
                            path=path,
                            protocol_name=protocol_name,
                            summary=summary,
                        )
                except Exception as exc:
                    messagebox.showerror("Invalid Opentrons step", str(exc))
                    return

                self._recipe[index] = self._preserve_recipe_item_metadata(item, new_item)
                self._refresh()
                win.destroy()

        else:
            ttk.Label(win, text="Robot host/IP:").grid(row=0, column=0, **pad, sticky="e")
            host_var = tk.StringVar(value=fields["robot_host"])
            ttk.Entry(win, textvariable=host_var, width=24).grid(row=0, column=1, **pad, sticky="w")

            ttk.Label(win, text="Robot API port:").grid(row=0, column=2, **pad, sticky="e")
            port_var = tk.StringVar(value=fields["robot_port"])
            ttk.Entry(win, textvariable=port_var, width=10).grid(row=0, column=3, **pad, sticky="w")

            ttk.Label(
                win,
                text="This row will send an OT-2 home command to the configured robot target.",
                foreground="#1f4e79",
                wraplength=640,
                justify="left",
            ).grid(row=1, column=0, columnspan=4, padx=6, pady=(0, 4), sticky="w")

            btns = ttk.Frame(win)
            btns.grid(row=2, column=0, columnspan=4, pady=(6, 8))

            def _apply():
                try:
                    host, port = _parse_robot_target(host_var.get(), port_var.get())
                    new_item = self._build_opentrons_home_item(host=host, port=port)
                except Exception as exc:
                    messagebox.showerror("Invalid Opentrons step", str(exc))
                    return

                self._recipe[index] = self._preserve_recipe_item_metadata(item, new_item)
                self._refresh()
                win.destroy()

        ttk.Button(btns, text="Update", command=_apply).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda _e: _apply())
        win.bind("<Escape>", lambda _e: win.destroy())

    def _is_pump_editable(self, item: dict) -> bool:
        item_type = (item.get("type") or "").upper()
        return item_type.startswith("PUMP_") or item_type in ("PAUSE", "ALERT")

    def _extract_pump_fields(self, item: dict) -> dict:
        item_type = (item.get("type") or "").upper()
        if item_type == "PAUSE":
            return {
                "action": "WAIT",
                "units": "uLmin",
                "mode": "infuse",
                "diameter_mm": 11.73,
                "rate": 1.0,
                "volume": 25.0,
                "delay_min": 0.0,
                "cmd": "",
                "wait": float(item.get("pause_seconds", 11.0)),
                "alert": "Check setup",
                "target_eta_s": float(FLOWCELL_FILL_TARGET_S),
                "track_collection": False,
                "collection_capacity_ml": float(COLLECTION_SYRINGE_CAPACITY_ML),
                "collection_warn_ml": float(default_collection_warn_ml(COLLECTION_SYRINGE_CAPACITY_ML)),
            }
        if item_type == "ALERT":
            return {
                "action": "ALERT",
                "units": "uLmin",
                "mode": "infuse",
                "diameter_mm": 11.73,
                "rate": 1.0,
                "volume": 25.0,
                "delay_min": 0.0,
                "cmd": "",
                "wait": 11.0,
                "alert": str(item.get("alert_message") or ""),
                "target_eta_s": float(FLOWCELL_FILL_TARGET_S),
                "track_collection": False,
                "collection_capacity_ml": float(COLLECTION_SYRINGE_CAPACITY_ML),
                "collection_warn_ml": float(default_collection_warn_ml(COLLECTION_SYRINGE_CAPACITY_ML)),
            }

        action_info = item.get("pump_action") or {}
        action = (action_info.get("name") or item_type.replace("PUMP_", "")).upper()
        params = action_info.get("params") or {}
        return {
            "action": action,
            "units": str(params.get("units", "uLmin")),
            "mode": str(params.get("mode", "infuse")),
            "diameter_mm": float(params.get("diameter_mm", 11.73)),
            "rate": float(params.get("rate", 1.0)),
            "volume": float(params.get("volume", 25.0)),
            "delay_min": float(params.get("delay_min", 0.0)),
            "cmd": str(params.get("cmd", "")),
            "wait": 11.0,
            "alert": "Check setup",
            "target_eta_s": float(params.get("target_eta_s", FLOWCELL_FILL_TARGET_S)),
            "track_collection": bool(params.get("track_collection", False)),
            "collection_capacity_ml": float(params.get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML)),
            "collection_warn_ml": float(params.get("collection_warn_ml", default_collection_warn_ml(params.get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML)))),
        }

    def _edit_pump_step(self, index: int):
        item = self._recipe[index]
        fields = self._extract_pump_fields(item)
        from config import SYRINGE_PRESETS_MM

        win = tk.Toplevel(self._frame)
        win.title("Edit Pump Step")
        win.transient(self._frame.winfo_toplevel())
        win.grab_set()

        pad = {"padx": 6, "pady": 4}
        ttk.Label(win, text="Pump action:").grid(row=0, column=0, **pad, sticky="e")
        action_var = tk.StringVar(value=fields["action"])
        ttk.Combobox(
            win,
            textvariable=action_var,
            values=[
                "HEXW2",
                "APPLY",
                "COMMAND",
                "START",
                "PAUSE",
                "STOP",
                "RESTART",
                "STATUS",
                "STATUS_PORT",
                "STATE_RESET",
                "WAIT",
                "ALERT",
            ],
            width=16,
            state="readonly",
        ).grid(row=0, column=1, **pad, sticky="w")

        ttk.Label(win, text="Units:").grid(row=0, column=2, **pad, sticky="e")
        units_var = tk.StringVar(value=fields["units"])
        ttk.Combobox(
            win,
            textvariable=units_var,
            values=["mLmin", "mLhr", "uLmin", "uLhr"],
            width=10,
            state="readonly",
        ).grid(row=0, column=3, **pad, sticky="w")

        ttk.Label(win, text="Mode:").grid(row=0, column=4, **pad, sticky="e")
        mode_var = tk.StringVar(value=fields["mode"])
        ttk.Combobox(
            win,
            textvariable=mode_var,
            values=["infuse", "withdraw"],
            width=10,
            state="readonly",
        ).grid(row=0, column=5, **pad, sticky="w")

        ttk.Label(win, text="Diameter (mm) (syringe ID):").grid(row=0, column=6, **pad, sticky="e")
        diameter_var = tk.DoubleVar(value=fields["diameter_mm"])
        ttk.Entry(win, width=10, textvariable=diameter_var).grid(row=0, column=7, **pad, sticky="w")

        preferred_syringe = "Custom"
        if abs(float(fields["collection_capacity_ml"]) - 5.0) < 0.01:
            preferred_syringe = "5 mL (typical)"
        ttk.Label(win, text="Syringe preset:").grid(row=2, column=0, **pad, sticky="e")
        syringe_var = tk.StringVar(value=preferred_syringe)
        syringe_values = ["Custom"] + sorted(SYRINGE_PRESETS_MM.keys())
        syringe_combo = ttk.Combobox(
            win,
            textvariable=syringe_var,
            values=syringe_values,
            width=22,
            state="readonly",
        )
        syringe_combo.grid(row=2, column=1, columnspan=2, **pad, sticky="w")
        ttk.Label(win, text="Tip: Diameter = syringe inner diameter (ID).", foreground="#666").grid(
            row=2, column=3, columnspan=5, padx=6, pady=4, sticky="w"
        )

        def _apply_preset(_e=None):
            key = (syringe_var.get() or "").strip()
            if not key or key == "Custom":
                return
            mm = SYRINGE_PRESETS_MM.get(key)
            if mm is None:
                return
            try:
                diameter_var.set(float(mm))
            except Exception:
                pass
            _refresh_popup_computed()

        syringe_combo.bind("<<ComboboxSelected>>", _apply_preset)

        ttk.Label(win, text="Calculated rate:").grid(row=1, column=0, **pad, sticky="e")
        rate_var = tk.DoubleVar(value=fields["rate"])
        rate_text_var = tk.StringVar(value="Calculated rate: -")
        ttk.Label(win, textvariable=rate_text_var, foreground="#555").grid(
            row=1, column=1, **pad, sticky="w"
        )

        ttk.Label(win, text="Volume:").grid(row=1, column=2, **pad, sticky="e")
        volume_var = tk.DoubleVar(value=fields["volume"])
        ttk.Entry(win, width=10, textvariable=volume_var).grid(row=1, column=3, **pad, sticky="w")

        ttk.Label(win, text="Delay (min):").grid(row=1, column=4, **pad, sticky="e")
        delay_var = tk.DoubleVar(value=fields["delay_min"])
        ttk.Entry(win, width=10, textvariable=delay_var).grid(row=1, column=5, **pad, sticky="w")

        ttk.Label(win, text="Wait (sec):").grid(row=1, column=6, **pad, sticky="e")
        wait_var = tk.DoubleVar(value=fields["wait"])
        ttk.Entry(win, width=10, textvariable=wait_var).grid(row=1, column=7, **pad, sticky="w")

        ttk.Label(win, text="Target ETA (s):").grid(row=3, column=0, **pad, sticky="e")
        target_eta_var = tk.DoubleVar(value=fields["target_eta_s"])
        ttk.Entry(win, width=10, textvariable=target_eta_var).grid(row=3, column=1, **pad, sticky="w")

        def _apply_flowcell_preset():
            action_var.set("HEXW2")
            units_var.set("uLmin")
            mode_var.set("withdraw")
            volume_var.set(float(FLOWCELL_FILL_VOLUME_UL))
            target_eta_var.set(float(FLOWCELL_FILL_TARGET_S))
            track_collection_var.set(True)
            try:
                syringe_var.set("5 mL (typical)")
                diameter_var.set(float(SYRINGE_PRESETS_MM["5 mL (typical)"]))
            except Exception:
                pass
            _refresh_popup_computed()

        ttk.Button(win, text="Preset Flowcell Pull", command=_apply_flowcell_preset).grid(
            row=3, column=2, columnspan=2, **pad, sticky="w"
        )

        track_collection_var = tk.BooleanVar(value=fields["track_collection"])
        ttk.Checkbutton(win, text="Track collected volume (Recommended)", variable=track_collection_var).grid(
            row=3, column=5, columnspan=2, padx=6, pady=4, sticky="w"
        )

        capacity_var = tk.DoubleVar(value=fields["collection_capacity_ml"])
        warn_var = tk.DoubleVar(value=fields["collection_warn_ml"])
        collection_text_var = tk.StringVar(value="Collection syringe: -")
        ttk.Label(win, text="Collection syringe:").grid(row=4, column=0, **pad, sticky="e")
        ttk.Label(win, textvariable=collection_text_var, foreground="#555").grid(
            row=4, column=1, columnspan=3, **pad, sticky="w"
        )

        eta_label = ttk.Label(win, text="ETA: -", foreground="#555")
        eta_label.grid(row=4, column=4, columnspan=4, padx=6, pady=4, sticky="w")

        def _update_eta_label(*_args):
            eta_s = estimate_eta_seconds(
                self._safe_float_var(volume_var, 0.0),
                self._safe_float_var(rate_var, 0.0),
                units_var.get(),
            )
            if eta_s is None:
                eta_label.configure(text="ETA: -")
                return
            extra = ""
            if bool(track_collection_var.get()):
                volume_ul = volume_to_ul(self._safe_float_var(volume_var, 0.0), units_var.get())
                if volume_ul is not None:
                    extra = f" | collect {volume_ul / 1000.0:.3f} mL"
            eta_label.configure(text=f"ETA: {eta_s:.1f}s{extra}")

        def _refresh_popup_computed(*_args):
            computed = self._computed_pump_values(
                volume=self._safe_float_var(volume_var, FLOWCELL_FILL_VOLUME_UL),
                units=str(units_var.get() or "uLmin"),
                target_eta_s=self._safe_float_var(target_eta_var, FLOWCELL_FILL_TARGET_S),
                syringe_label=str(syringe_var.get() or ""),
            )
            capacity_var.set(float(computed["capacity_ml"]))
            warn_var.set(float(computed["warn_ml"]))
            if computed["rate"] is None:
                rate_text_var.set("Calculated rate: -")
            else:
                rate_var.set(float(computed["rate"]))
                rate_text_var.set(f"Calculated rate: {computed['rate']:.1f} {units_var.get()}")
            collection_text_var.set(f"{computed['capacity_ml']:g} mL | warning at {computed['warn_ml']:g} mL")
            _update_eta_label()

        for var in (units_var, volume_var, target_eta_var, track_collection_var, syringe_var):
            try:
                var.trace_add("write", _refresh_popup_computed)
            except Exception:
                pass
        _apply_preset()
        _refresh_popup_computed()

        ttk.Label(win, text="Raw cmd:").grid(row=5, column=0, **pad, sticky="e")
        cmd_var = tk.StringVar(value=fields["cmd"])
        ttk.Entry(win, width=60, textvariable=cmd_var).grid(row=5, column=1, columnspan=7, **pad, sticky="w")

        ttk.Label(win, text="Alert message:").grid(row=6, column=0, **pad, sticky="e")
        alert_var = tk.StringVar(value=fields["alert"])
        ttk.Entry(win, width=60, textvariable=alert_var).grid(row=6, column=1, columnspan=7, **pad, sticky="w")

        btns = ttk.Frame(win)
        btns.grid(row=7, column=0, columnspan=8, pady=(6, 8))

        def _apply():
            try:
                new_item = self._build_pump_item(
                    action=action_var.get(),
                    units=str(units_var.get()),
                    mode=str(mode_var.get()),
                    diameter_mm=self._safe_float_var(diameter_var, 11.73),
                    rate=self._safe_float_var(rate_var, 0.0),
                    volume=self._safe_float_var(volume_var, FLOWCELL_FILL_VOLUME_UL),
                    delay_min=self._safe_float_var(delay_var, 0.0),
                    cmd=(cmd_var.get() or "").strip(),
                    wait=self._safe_float_var(wait_var, 11.0),
                    alert=(alert_var.get() or "").strip(),
                    target_eta_s=self._safe_float_var(target_eta_var, FLOWCELL_FILL_TARGET_S),
                    track_collection=bool(track_collection_var.get()),
                    collection_capacity_ml=self._safe_float_var(capacity_var, COLLECTION_SYRINGE_CAPACITY_ML),
                    collection_warn_ml=self._safe_float_var(
                        warn_var,
                        default_collection_warn_ml(self._safe_float_var(capacity_var, COLLECTION_SYRINGE_CAPACITY_ML)),
                    ),
                )
            except Exception as exc:
                messagebox.showerror("Invalid pump step", str(exc))
                return

            self._recipe[index] = self._preserve_recipe_item_metadata(item, new_item)
            self._refresh()
            win.destroy()

        ttk.Button(btns, text="Update", command=_apply).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda _e: _apply())
        win.bind("<Escape>", lambda _e: win.destroy())

    # ── Save / load ────────────────────────────────────────────────────────

    def _save_recipe(self):
        if not self._recipe:
            messagebox.showwarning("Empty", "No recipe items to save.")
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            filetypes=[("JSON", "*.json")],
            initialdir=str(self._recipe_root),
        )
        if not path:
            return
        errors, warnings = self._validate_recipe()
        if errors:
            messagebox.showerror("Recipe Has Errors", "\n".join(errors))
            return
        if warnings:
            proceed = messagebox.askyesno(
                "Recipe Warnings",
                "The recipe has warnings:\n\n"
                + "\n".join(f"- {msg}" for msg in warnings[:10])
                + "\n\nSave anyway?",
            )
            if not proceed:
                return
        migrated_items, _ = self._migrate_recipe_resume_keys(self._recipe)
        payload = {"items": self._compact_recipe_items(migrated_items)}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2)

    def _load_recipe(self):
        path = filedialog.askopenfilename(
            filetypes=[("JSON", "*.json")],
            initialdir=str(self._recipe_root),
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            items = payload.get("items", [])
            if not isinstance(items, list):
                raise ValueError("Invalid recipe format: items is not a list.")
            migrated_items, changed = self._migrate_recipe_resume_keys(items)
            self._recipe = [copy.deepcopy(item) for item in migrated_items]
            self._refresh()
            if changed:
                messagebox.showinfo(
                    "Recipe Updated",
                    f"Migrated {changed} Opentrons resume key entr{'y' if changed == 1 else 'ies'} to the new format while loading.",
                )
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    # -----Blocks ---------------------------------------------

    def _build_blocks_library(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=6)
        misc = ttk.LabelFrame(parent, text="Miscellaneous")
        misc.pack(fill="x", padx=6, pady=(0, 6))
        self._misc_folder_mode_var = tk.StringVar(value="current_session")
        self._misc_folder_path_var = tk.StringVar(value="")
        ttk.Radiobutton(
            misc,
            text="Current session folder",
            variable=self._misc_folder_mode_var,
            value="current_session",
        ).grid(row=0, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        ttk.Radiobutton(
            misc,
            text="Current experiment folder",
            variable=self._misc_folder_mode_var,
            value="current_experiment",
        ).grid(row=1, column=0, columnspan=2, padx=6, pady=4, sticky="w")
        ttk.Radiobutton(
            misc,
            text="Specific folder",
            variable=self._misc_folder_mode_var,
            value="specific_folder",
        ).grid(row=2, column=0, padx=6, pady=4, sticky="w")
        ttk.Entry(misc, textvariable=self._misc_folder_path_var, width=60).grid(row=2, column=1, padx=6, pady=4, sticky="ew")
        ttk.Button(misc, text="Browse", command=self._browse_misc_folder).grid(row=2, column=2, padx=6, pady=4, sticky="w")
        ttk.Button(misc, text="Add Compress + Send", command=self._add_compress_send_block).grid(
            row=0, column=3, rowspan=3, padx=6, pady=4, sticky="e"
        )
        misc.columnconfigure(1, weight=1)
        ttk.Button(top, text="Refresh Blocks",
                   command=self._load_blocks).pack(side="left", padx=4)
        ttk.Button(top, text="Add Block",
                   command=self._add_selected_block).pack(side="left", padx=4)
        ttk.Label(top, text="View:").pack(side="left", padx=(12, 2))
        self._block_filter = tk.StringVar(value="All")
        ttk.Combobox(
            top,
            textvariable=self._block_filter,
            values=["All", "Default", "Custom", "Saved"],
            state="readonly",
            width=10,
        ).pack(side="left", padx=4)
        self._block_filter.trace_add("write", lambda *_: self._load_blocks())

        cols = ("Block", "Items")
        self._block_tree = ttk.Treeview(parent, columns=cols, show="headings", height=8)
        self._block_tree.heading("Block", text="Block")
        self._block_tree.heading("Items", text="Items")
        self._block_tree.column("Block", width=200)
        self._block_tree.column("Items", width=560)
        self._block_tree.pack(fill="both", expand=True, padx=6, pady=6)

        self._blocks: dict = {}
        self._block_iid_to_name: dict = {}
        self._load_blocks()

        hint = ttk.Label(
            parent,
            text=(
                "Blocks are predefined sequences stored in recipe_maker/default_blocks/, "
                "recipe_maker/custom_blocks/, and recipe_maker/saved_recipes/."
            ),
            foreground="#666",
        )
        hint.pack(side="bottom", anchor="w", padx=8, pady=(0, 6))

    def _browse_misc_folder(self):
        initial_dir = str((self._repo_root / "measurement_data").resolve())
        selected = filedialog.askdirectory(title="Select Folder to Compress", initialdir=initial_dir)
        if selected:
            self._misc_folder_mode_var.set("specific_folder")
            self._misc_folder_path_var.set(selected)

    def _add_compress_send_block(self):
        mode = (self._misc_folder_mode_var.get() or "current_session").strip()
        folder_path = (self._misc_folder_path_var.get() or "").strip()
        if mode == "specific_folder" and not folder_path:
            messagebox.showwarning("Missing Folder", "Choose a folder first.")
            return
        details = (
            f"Compress + send folder: {folder_path}"
            if mode == "specific_folder"
            else (
                "Compress + send current session folder"
                if mode == "current_session"
                else "Compress + send current experiment folder"
            )
        )
        item = {
            "type": "MISC_COMPRESS_SEND",
            "status": "pending",
            "details": details,
            "misc_action": {
                "name": "COMPRESS_SEND",
                "params": {
                    "folder_mode": mode,
                    "folder_path": folder_path,
                    "dest_dir": str(self._COMPRESS_SEND_DEST_DIR),
                    "batch_path": r"C:\Users\chienlab\Desktop\Compress_n_SendToDrive.bat",
                },
            },
        }
        self._recipe.append(item)
        self._refresh()

    def _load_blocks(self):
        self._blocks.clear()
        self._block_iid_to_name.clear()
        for row in self._block_tree.get_children():
            self._block_tree.delete(row)

        view = (getattr(self, "_block_filter", tk.StringVar(value="All")).get() or "All").lower()
        if view == "default":
            blocks_dirs = [self._default_blocks_dir]
        elif view == "custom":
            blocks_dirs = [self._custom_blocks_dir]
        elif view == "saved":
            blocks_dirs = [self._saved_blocks_dir]
        else:
            blocks_dirs = [self._default_blocks_dir, self._custom_blocks_dir, self._saved_blocks_dir]
        files = []
        for blocks_dir in blocks_dirs:
            if not blocks_dir.exists():
                continue
            files.extend(list(blocks_dir.glob("*.json")) + list(blocks_dir.glob("*.JSON")))
        seen = set()
        for path in sorted(files):
            norm = path.resolve().as_posix().lower()
            if norm in seen:
                continue
            seen.add(norm)
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception as exc:
                self._block_tree.insert(
                    "", "end",
                    values=(f"Invalid JSON: {path.name}", str(exc)),
                )
                continue

            items = payload.get("items", [])
            if not isinstance(items, list):
                self._block_tree.insert(
                    "", "end",
                    values=(f"Invalid block: {path.name}", "Missing items[]"),
                )
                continue

            name = payload.get("name") or path.stem
            if name in self._blocks:
                name = f"{name} ({path.parent.name})"
            self._blocks[name] = items
            summary = ", ".join(item.get("type", "") for item in items[:5])
            if len(items) > 5:
                summary += f" (+{len(items) - 5})"
            iid = self._block_tree.insert("", "end", values=(name, summary))
            self._block_iid_to_name[iid] = name

        if not self._blocks:
            self._block_tree.insert(
                "", "end",
                values=("No blocks found", "recipe_maker/default_blocks, custom_blocks, or saved_recipes"),
            )

    def _add_selected_block(self):
        sel = self._block_tree.selection()
        if not sel:
            messagebox.showwarning("No selection", "Select a block to add.")
            return
        name = self._block_iid_to_name.get(sel[0])
        if not name:
            return
        items = self._blocks.get(name, [])
        if not items:
            return
        for item in items:
            cloned = dict(item)
            cloned.setdefault("status", "pending")
            cloned["block_name"] = name
            self._recipe.append(cloned)
        self._refresh()
