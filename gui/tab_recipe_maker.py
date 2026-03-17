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
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk, simpledialog

from methods import library_map
from config import BLOCKS_DIR


class RecipeMakerTab:
    """Manages the 'Recipe Maker' notebook tab."""

    def __init__(self, parent_frame, on_send_to_queue=None):
        self._frame = parent_frame
        self._on_send_to_queue = on_send_to_queue
        self._recipe: list = []
        self._clipboard: list = []
        self._method_entries: dict = {}
        self._method_iid_to_key: dict = {}
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
        ttk.Button(ctrl, text="Clear",
                   command=self._clear_recipe).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="Send to Queue",
                   command=self._send_to_queue).pack(side="left", padx=4)


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
        block_tab = ttk.Frame(bottom_nb)
        bottom_nb.add(pump_tab, text="Pump Steps")
        bottom_nb.add(method_tab, text="Method Library")
        bottom_nb.add(block_tab, text="Blocks")

        self._build_pump_editor(pump_tab)
        self._build_method_library(method_tab)
        self._build_blocks_library(block_tab)

    def _legend_chip(self, parent, color: str, text: str):
        swatch = tk.Canvas(parent, width=12, height=12, highlightthickness=0)
        swatch.create_rectangle(0, 0, 12, 12, fill=color, outline="#777")
        swatch.pack(side="left", padx=(8, 2))
        ttk.Label(parent, text=text).pack(side="left", padx=(0, 6))

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
        self._pump_syringe = tk.StringVar(value="Custom")
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

        syringe_combo.bind("<<ComboboxSelected>>", _apply_preset)
        ttk.Label(parent, text="Tip: Diameter = syringe inner diameter (ID).", foreground="#666").grid(
            row=1, column=3, columnspan=5, padx=6, pady=4, sticky="w"
        )

        ttk.Label(parent, text="Rate:").grid(row=2, column=0, **pad, sticky="e")
        self._pump_rate = tk.DoubleVar(value=1.0)
        ttk.Entry(parent, width=10, textvariable=self._pump_rate).grid(row=2, column=1, **pad, sticky="w")

        ttk.Label(parent, text="Volume:").grid(row=2, column=2, **pad, sticky="e")
        self._pump_volume = tk.DoubleVar(value=25.0)
        ttk.Entry(parent, width=10, textvariable=self._pump_volume).grid(row=2, column=3, **pad, sticky="w")

        ttk.Label(parent, text="Delay (min):").grid(row=2, column=4, **pad, sticky="e")
        self._pump_delay_min = tk.DoubleVar(value=0.0)
        ttk.Entry(parent, width=10, textvariable=self._pump_delay_min).grid(row=2, column=5, **pad, sticky="w")

        ttk.Label(parent, text="Wait (sec):").grid(row=2, column=6, **pad, sticky="e")
        self._wait_seconds = tk.DoubleVar(value=10.0)
        ttk.Entry(parent, width=10, textvariable=self._wait_seconds).grid(row=2, column=7, **pad, sticky="w")

        ttk.Label(parent, text="Raw cmd:").grid(row=3, column=0, **pad, sticky="e")
        self._pump_raw_cmd = tk.StringVar(value="")
        ttk.Entry(parent, width=60, textvariable=self._pump_raw_cmd).grid(
            row=3, column=1, columnspan=7, **pad, sticky="w"
        )

        ttk.Label(parent, text="Alert message:").grid(row=4, column=0, **pad, sticky="e")
        self._alert_message = tk.StringVar(value="Check setup")
        ttk.Entry(parent, width=60, textvariable=self._alert_message).grid(
            row=4, column=1, columnspan=7, **pad, sticky="w"
        )

        ttk.Label(
            parent,
            text="Tip: Only relevant fields are used based on action type.",
            foreground="#666",
        ).grid(row=5, column=0, columnspan=8, padx=6, pady=(0, 6), sticky="w")

    def _add_pump_step(self):
        action = self._pump_action.get().strip().upper()
        if not action:
            return
        try:
            item = self._build_pump_item(
                action=action,
                units=str(self._pump_units.get()),
                mode=str(self._pump_mode.get()),
                diameter_mm=float(self._pump_diameter_mm.get()),
                rate=float(self._pump_rate.get()),
                volume=float(self._pump_volume.get()),
                delay_min=float(self._pump_delay_min.get()),
                cmd=(self._pump_raw_cmd.get() or "").strip(),
                wait=float(self._wait_seconds.get()),
                alert=(self._alert_message.get() or "").strip(),
            )
        except Exception as exc:
            messagebox.showerror("Invalid pump step", str(exc))
            return
        self._recipe.append(item)
        self._refresh()

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
        start = simpledialog.askinteger(
            "Select Range",
            f"Start row (1-{total}):",
            minvalue=1, maxvalue=total
        )
        if start is None:
            return
        end = simpledialog.askinteger(
            "Select Range",
            f"End row (1-{total}):",
            minvalue=1, maxvalue=total
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
        for item in self._recipe:
            cloned = copy.deepcopy(item)
            cloned["status"] = "pending"
            self._on_send_to_queue(cloned)

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
    ) -> dict:
        action = (action or "").strip().upper()
        if not action:
            raise ValueError("Pump action is required.")

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

        details = ""
        pump_action = {"name": action, "params": {}}

        if action == "COMMAND":
            if not cmd:
                raise ValueError("Raw cmd cannot be empty for COMMAND action.")
            details = f"Pump cmd: {cmd}"
            pump_action["params"] = {"cmd": cmd}

        elif action == "APPLY":
            details = f"Pump: Apply ({units}, {mode}, Ø{diameter_mm:g}mm)"
            pump_action["params"] = {
                "units": units,
                "mode": mode,
                "diameter_mm": float(diameter_mm),
                "rate": float(rate),
                "volume": float(volume),
            }

        elif action == "HEXW2":
            details = f"Pump: HEXW2 {mode} {volume:g} @ {rate:g} ({units})"
            pump_action["params"] = {
                "units": units,
                "mode": mode,
                "diameter_mm": float(diameter_mm),
                "volume": float(volume),
                "rate": float(rate),
                "delay_min": float(delay_min),
                "start": True,
            }

        elif action in {"START", "PAUSE", "STOP", "RESTART", "STATUS", "STATUS_PORT"}:
            details = f"Pump: {action.replace('_', ' ').title()}"
            pump_action["params"] = {}

        else:
            raise ValueError(f"Unsupported pump action: {action}")

        return {
            "type": f"PUMP_{action}",
            "status": "pending",
            "details": details,
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

            if name == "COMMAND":
                cmd = str(params.get("cmd") or "").strip()
                item["details"] = f"Pump cmd: {cmd}" if cmd else "Pump cmd"
                continue

            if name == "APPLY":
                units = str(params.get("units") or "")
                mode = str(params.get("mode") or "")
                diam = params.get("diameter_mm")
                try:
                    diam_s = f"{float(diam):g}mm" if diam is not None else "?mm"
                except Exception:
                    diam_s = "?mm"
                item["details"] = f"Pump: Apply ({units}, {mode}, Ø{diam_s})"
                continue

            if name == "HEXW2":
                units = str(params.get("units") or "")
                mode = str(params.get("mode") or "")
                try:
                    volume = float(params.get("volume"))
                except Exception:
                    volume = None
                try:
                    rate = float(params.get("rate"))
                except Exception:
                    rate = None
                if volume is None or rate is None:
                    item["details"] = f"Pump: HEXW2 ({mode})"
                else:
                    item["details"] = f"Pump: HEXW2 {mode} {volume:g} @ {rate:g} ({units})"
                continue

            if name in {"START", "PAUSE", "STOP", "RESTART", "STATUS", "STATUS_PORT"}:
                item["details"] = f"Pump: {name.replace('_', ' ').title()}"
                continue

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
        if not self._is_pump_editable(item):
            return
        self._edit_pump_step(idx)

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
                "wait": float(item.get("pause_seconds", 10.0)),
                "alert": "Check setup",
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
                "wait": 10.0,
                "alert": str(item.get("alert_message") or ""),
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
            "wait": 10.0,
            "alert": "Check setup",
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

        ttk.Label(win, text="Syringe preset:").grid(row=2, column=0, **pad, sticky="e")
        syringe_var = tk.StringVar(value="Custom")
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

        syringe_combo.bind("<<ComboboxSelected>>", _apply_preset)

        ttk.Label(win, text="Rate:").grid(row=1, column=0, **pad, sticky="e")
        rate_var = tk.DoubleVar(value=fields["rate"])
        ttk.Entry(win, width=10, textvariable=rate_var).grid(row=1, column=1, **pad, sticky="w")

        ttk.Label(win, text="Volume:").grid(row=1, column=2, **pad, sticky="e")
        volume_var = tk.DoubleVar(value=fields["volume"])
        ttk.Entry(win, width=10, textvariable=volume_var).grid(row=1, column=3, **pad, sticky="w")

        ttk.Label(win, text="Delay (min):").grid(row=1, column=4, **pad, sticky="e")
        delay_var = tk.DoubleVar(value=fields["delay_min"])
        ttk.Entry(win, width=10, textvariable=delay_var).grid(row=1, column=5, **pad, sticky="w")

        ttk.Label(win, text="Wait (sec):").grid(row=1, column=6, **pad, sticky="e")
        wait_var = tk.DoubleVar(value=fields["wait"])
        ttk.Entry(win, width=10, textvariable=wait_var).grid(row=1, column=7, **pad, sticky="w")

        ttk.Label(win, text="Raw cmd:").grid(row=3, column=0, **pad, sticky="e")
        cmd_var = tk.StringVar(value=fields["cmd"])
        ttk.Entry(win, width=60, textvariable=cmd_var).grid(row=3, column=1, columnspan=7, **pad, sticky="w")

        ttk.Label(win, text="Alert message:").grid(row=4, column=0, **pad, sticky="e")
        alert_var = tk.StringVar(value=fields["alert"])
        ttk.Entry(win, width=60, textvariable=alert_var).grid(row=4, column=1, columnspan=7, **pad, sticky="w")

        btns = ttk.Frame(win)
        btns.grid(row=5, column=0, columnspan=8, pady=(6, 8))

        def _apply():
            try:
                new_item = self._build_pump_item(
                    action=action_var.get(),
                    units=str(units_var.get()),
                    mode=str(mode_var.get()),
                    diameter_mm=float(diameter_var.get()),
                    rate=float(rate_var.get()),
                    volume=float(volume_var.get()),
                    delay_min=float(delay_var.get()),
                    cmd=(cmd_var.get() or "").strip(),
                    wait=float(wait_var.get()),
                    alert=(alert_var.get() or "").strip(),
                )
            except Exception as exc:
                messagebox.showerror("Invalid pump step", str(exc))
                return

            for key in ("block_name", "block_ref"):
                if key in item and key not in new_item:
                    new_item[key] = item[key]
            if "status" in item:
                new_item["status"] = item.get("status")

            self._recipe[index] = new_item
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
        payload = {"items": self._recipe}
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
            self._recipe = items
            self._refresh()
        except Exception as exc:
            messagebox.showerror("Load failed", str(exc))

    # -----Blocks ---------------------------------------------

    def _build_blocks_library(self, parent):
        top = ttk.Frame(parent)
        top.pack(fill="x", padx=6, pady=6)
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
