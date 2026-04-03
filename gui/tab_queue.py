"""
gui/tab_queue.py — Queue & Execution tab.

Responsible for:
  - Displaying the measurement queue in a Treeview
  - Copy / paste / duplicate / delete / reorder queue items
  - Save / load queue to JSON
  - Running / stopping the queue
  - Executing each queue item type (measurement, pause, alert, pump)
  - Session info bar (measurement counter, script registry size)
"""

import copy
import json
import re
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox
import tkinter as tk
from tkinter import ttk, scrolledtext, simpledialog
from typing import Optional

from config import (
    OPENTRONS_PROTOCOLS_DIR,
    OPENTRONS_DEFAULT_API_PORT,
    OPENTRONS_DEFAULT_HOST,
    CHEMYX_DEFAULT_PORT,
    COLLECTION_SYRINGE_CAPACITY_ML,
    FLOWCELL_FILL_VOLUME_UL,
    FLOWCELL_FILL_TARGET_S,
    SYRINGE_PRESETS_MM,
)
from core.runner import SerialMeasurementRunner
from core.pump_step_utils import (
    build_pump_details,
    default_collection_warn_ml,
    estimate_eta_seconds,
    format_ml_from_ul,
    rate_for_target_eta,
    volume_to_ul,
)
from methods import library_map
from core.session import SessionState
from robot import OpentronsProtocolRunner


class QueueTab:
    """Manages the 'Queue & Execution' notebook tab.

    Parameters
    ----------
    parent_frame:
        The ``ttk.Frame`` added to the notebook for this tab.
    session:
        Shared :class:`~core.session.SessionState`.
    plotter:
        Reference to :class:`~gui.tab_plotter.PlotterTab` for live plotting.
    pump_ctrl:
        Optional pump controller (may be ``None`` on 64-bit / no hardware).
    root:
        The root ``tk.Tk`` window — needed for ``root.after()``.
    """

    def __init__(self, parent_frame, session: SessionState, plotter, pump_ctrl, root):
        self._frame      = parent_frame
        self._session    = session
        self._plotter    = plotter
        self._pump_ctrl  = pump_ctrl
        self._root       = root

        self._queue_thread = None
        self._reorder_pending  = False
        self._reorder_snapshot = None
        self._drag_item        = None
        self._clipboard:list   = []
        self._last_selected    = None
        self._last_queue_path  = None
        self._opentrons_paused_runs: dict[str, dict] = {}
        self._active_opentrons_target: dict[str, str | int | None] | None = None

        self._build()
        self._session.register_collection_state_listener(self._schedule_refresh_labels)
        self._schedule_refresh_labels()

    _TIP_WELL_RE = re.compile(r"^[A-Z]+[1-9][0-9]*$")

    # ── Build ─────────────────────────────────────────────────────────────────

    def _build(self):
        pane = ttk.PanedWindow(self._frame, orient=tk.VERTICAL)
        pane.pack(fill="both", expand=True)

        top    = ttk.Frame(pane); pane.add(top, weight=1)
        bottom = ttk.Frame(pane); pane.add(bottom, weight=1)

        # ── Control bar ───────────────────────────────────────────────────────
        ctrl = ttk.Frame(top)
        ctrl.pack(pady=8, fill="x", padx=10)

        ttk.Button(ctrl, text="▶ Run Queue",       command=self.run_queue).pack(side="left", padx=4)
        ttk.Button(ctrl, text="▶ From Selected",   command=self.run_from_selected).pack(side="left", padx=4)
        ttk.Button(ctrl, text="⏹ Stop",            command=self.stop_queue).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="💾 Save",            command=self.save_queue).pack(side="left", padx=4)
        ttk.Button(ctrl, text="📂 Load",            command=self.load_queue).pack(side="left", padx=4)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="📋 Copy",            command=self.copy_selected).pack(side="left", padx=2)
        ttk.Button(ctrl, text="📌 Paste",           command=self.paste_after_selected).pack(side="left", padx=2)
        ttk.Button(ctrl, text="⧉ Duplicate",       command=self.duplicate_selected).pack(side="left", padx=2)
        ttk.Separator(ctrl, orient="vertical").pack(side="left", fill="y", padx=6)
        ttk.Button(ctrl, text="🗑 Delete",          command=self.delete_selected).pack(side="left", padx=2)
        ttk.Button(ctrl, text="✓ Confirm Move",    command=self.confirm_reorder).pack(side="left", padx=4)
        ttk.Button(ctrl, text="🗑 Clear All",       command=self.clear_queue).pack(side="left", padx=4)

        # ── Treeview ──────────────────────────────────────────────────────────
        cols = ("Type", "Status", "Details")
        tree_frame = ttk.Frame(top)
        tree_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self._tree = ttk.Treeview(
            tree_frame, columns=cols, show="tree headings", height=10, selectmode="extended"
        )
        self._tree.heading("#0",      text="#")
        self._tree.heading("Type",    text="Type")
        self._tree.heading("Status",  text="Status")
        self._tree.heading("Details", text="Details")
        self._tree.column("#0",      width=50)
        self._tree.column("Type",    width=150)
        self._tree.column("Status",  width=100)
        self._tree.column("Details", width=400)
        self._tree.pack(side="left", fill="both", expand=True)
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self._tree.yview)
        tree_scroll.pack(side="right", fill="y")
        self._tree.configure(yscrollcommand=tree_scroll.set)

        # Drag reorder
        self._tree.bind("<ButtonPress-1>",   self._drag_start)
        self._tree.bind("<B1-Motion>",       self._drag_motion)
        self._tree.bind("<ButtonRelease-1>", self._drag_release)
        self._tree.bind("<Shift-Button-1>",  self._select_range)
        self._tree.bind("<Double-1>", self._on_tree_double_click)

        # Right-click context menu
        self._ctx = tk.Menu(self._tree, tearoff=0)
        self._ctx.add_command(label="Edit", command=self._edit_selected)
        self._ctx.add_command(label="📋 Copy",        command=self.copy_selected)
        self._ctx.add_command(label="📌 Paste After", command=self.paste_after_selected)
        self._ctx.add_command(label="⧉ Duplicate",   command=self.duplicate_selected)
        self._ctx.add_command(label="Select Range…",  command=self._select_range_prompt)
        self._ctx.add_separator()
        self._ctx.add_command(label="🗑 Delete",      command=self.delete_selected)
        self._tree.bind("<Button-3>", self._show_ctx)
        self._tree.bind("<Control-c>", lambda e: self.copy_selected())
        self._tree.bind("<Control-v>", lambda e: self.paste_after_selected())
        self._tree.bind("<Control-d>", lambda e: self.duplicate_selected())

        # ── Log panel ─────────────────────────────────────────────────────────
        log_frame = ttk.LabelFrame(bottom, text="Live Output Log")
        log_frame.pack(fill="both", expand=True, padx=10, pady=5)
        self._log_text = scrolledtext.ScrolledText(log_frame, wrap=tk.WORD, height=10)
        self._log_text.pack(fill="both", expand=True)
        self._log_text.config(state="disabled")

        # ── Session info bar ──────────────────────────────────────────────────
        info_bar = ttk.Frame(self._frame)
        info_bar.pack(side="bottom", fill="x", padx=10, pady=(0, 2))
        self._lbl_counter  = ttk.Label(info_bar, text="Measurements this session: 0",
                                       foreground="#555")
        self._lbl_counter.pack(side="left", padx=8)
        self._lbl_registry = ttk.Label(info_bar, text="Script registry: 0 unique",
                                       foreground="#555")
        self._lbl_registry.pack(side="left", padx=8)
        self._lbl_collection = ttk.Label(info_bar, text="Collection: 0 steps | 0.000 / 50.0 mL",
                                         foreground="#555")
        self._lbl_collection.pack(side="left", padx=8)
        self._lbl_collection_meta = ttk.Label(
            info_bar,
            text="Registry: warn at 40.0 mL | event: initialized",
            foreground="#777",
        )
        self._lbl_collection_meta.pack(side="left", padx=8)
        ttk.Button(info_bar, text="Reset Counter",
                   command=self._reset_counter).pack(side="right", padx=4)
        ttk.Button(info_bar, text="Reset Syringe State",
                   command=self._reset_syringe_state).pack(side="right", padx=4)
        ttk.Button(info_bar, text="Clear Registry",
                   command=self._clear_registry).pack(side="right", padx=4)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = ttk.Label(self._frame, text="Status: Ready", relief="sunken")
        self._status.pack(side="bottom", fill="x", padx=10, pady=5)

    # ── Public API (used by app.py and MethodTab) ─────────────────────────────

    def add_item(self, item: dict):
        """Append a queue item dict and refresh the display."""
        prepared = item
        if isinstance(item, dict):
            resolved = self._deserialize(item)
            if resolved is not None:
                prepared = resolved
        self._session.measurement_queue.append(prepared)
        self.refresh()
        self.log(f"Queue add: {prepared.get('details', prepared.get('type'))}")

    def refresh(self):
        """Rebuild the Treeview from session.measurement_queue."""
        for row in self._tree.get_children():
            self._tree.delete(row)
        for i, item in enumerate(self._session.measurement_queue):
            self._tree.insert(
                "", "end", iid=str(i), text=str(i + 1),
                values=(item["type"], item["status"].upper(), item.get("details", "")),
            )

    def set_status(self, msg: str):
        self._status.config(text=f"Status: {msg}")

    def log(self, msg: str):
        session_mgr = getattr(self._session, "session_manager", None)
        if session_mgr is not None:
            session_mgr.log(msg)
            return
        self._append_log_gui(msg)

    def _append_log_gui(self, msg: str):
        def _append():
            self._log_text.config(state="normal")
            self._log_text.insert(tk.END, msg + "\n")
            self._log_text.see(tk.END)
            self._log_text.config(state="disabled")
        self._root.after(0, _append)

    def clear_log(self):
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", tk.END)
        self._log_text.config(state="disabled")

    def refresh_labels(self):
        """Update session info bar labels."""
        self._lbl_counter.config(
            text=f"Measurements this session: {self._session.measurement_counter}")
        self._lbl_registry.config(
            text=f"Script registry: {self._session.registry.size} unique")
        self._lbl_collection.config(
            text=(
                "Collection: "
                f"{self._session.collection_steps} steps | "
                f"{self._session.collection_volume_ul / 1000.0:.3f} / "
                f"{self._session.collection_capacity_ul / 1000.0:.1f} mL"
            )
        )
        self._lbl_collection_meta.config(
            text=(
                "Registry: "
                f"warn at {self._session.collection_warn_ul / 1000.0:.1f} mL | "
                f"event: {self._session.collection_last_event} | "
                f"updated: {self._session.collection_updated_at or '-'}"
            )
        )

    def _schedule_refresh_labels(self) -> None:
        self._root.after(0, self.refresh_labels)


    # ── Session info bar buttons ──────────────────────────────────────────────

    def _reset_counter(self):
        self._session.reset_counter()
        self.refresh_labels()

    def _clear_registry(self):
        self._session.registry.clear()
        self.refresh_labels()

    def _reset_syringe_state(self):
        if not messagebox.askyesno(
            "Reset Syringe State",
            "Reset the persistent syringe state to 0 mL?\n"
            "Use this after the collection syringe has been emptied.",
        ):
            return
        self._session.reset_collection_tracking(reason="manual ui reset")
        self.refresh_labels()
        self.log("Syringe state reset to 0 mL.")

    # ── Copy / paste / duplicate ──────────────────────────────────────────────

    def _selected_indices(self) -> list:
        return sorted(
            self._tree.index(iid) for iid in self._tree.selection()
            if iid
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

    def _show_ctx(self, event):
        row = self._tree.identify_row(event.y)
        if row:
            self._tree.selection_set(row)
            self._last_selected = row
        try:
            self._ctx.tk_popup(event.x_root, event.y_root)
        finally:
            self._ctx.grab_release()

    def copy_selected(self):
        idxs = self._selected_indices()
        if not idxs:
            messagebox.showwarning("No Selection", "Select item(s) to copy.")
            return
        self._clipboard = [copy.deepcopy(self._session.measurement_queue[i]) for i in idxs]
        self.set_status(f"Copied {len(self._clipboard)} item(s)")

    def paste_after_selected(self):
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before editing.")
            return
        if not self._clipboard:
            messagebox.showwarning("Empty Clipboard", "Copy items first.")
            return
        idxs = self._selected_indices()
        pos  = (idxs[-1] + 1) if idxs else len(self._session.measurement_queue)
        new  = [copy.deepcopy(i) for i in self._clipboard]
        for item in new:
            item["status"] = "pending"
        self._session.measurement_queue[pos:pos] = new
        self.refresh()
        self.set_status(f"Pasted {len(new)} item(s) at position {pos + 1}")

    def duplicate_selected(self):
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before editing.")
            return
        idxs = self._selected_indices()
        if not idxs:
            messagebox.showwarning("No Selection", "Select item(s) to duplicate.")
            return
        for idx in reversed(idxs):
            dupe = copy.deepcopy(self._session.measurement_queue[idx])
            dupe["status"] = "pending"
            self._session.measurement_queue.insert(idx + 1, dupe)
        self.refresh()
        self.set_status(f"Duplicated {len(idxs)} item(s)")

    def delete_selected(self):
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before editing.")
            return
        idxs = self._selected_indices()
        if not idxs:
            messagebox.showwarning("No Selection", "Select item to delete.")
            return
        for idx in reversed(idxs):
            removed = self._session.measurement_queue.pop(idx)
            self.log(f"Queue item deleted: {removed.get('details', removed.get('type'))}")
        self.refresh()
        self.set_status(f"Deleted {len(idxs)} item(s)")

    def clear_queue(self):
        self._reset_reorder()
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before clearing.")
            return
        self._session.measurement_queue.clear()
        self.refresh()
        self.set_status("Queue cleared")
        self.log("Queue cleared.")

    def _on_tree_double_click(self, event):
        row = self._tree.identify_row(event.y)
        if not row:
            return
        try:
            idx = self._tree.index(row)
        except Exception:
            return
        self._edit_queue_item(idx)

    def _edit_selected(self):
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before editing.")
            return
        idxs = self._selected_indices()
        if len(idxs) != 1:
            messagebox.showwarning("Select One", "Select a single queue item to edit.")
            return
        self._edit_queue_item(idxs[0])

    @staticmethod
    def _is_editable_item(item: dict) -> bool:
        item_type = str(item.get("type") or "").upper()
        return item_type.startswith("PUMP_") or item_type in {"PAUSE", "ALERT"}

    @staticmethod
    def _extract_edit_fields(item: dict) -> dict:
        item_type = str(item.get("type") or "").upper()
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
        params = dict(action_info.get("params") or {})
        action = str(action_info.get("name") or item_type.replace("PUMP_", "")).upper()
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

    @staticmethod
    def _build_edited_queue_item(fields: dict) -> dict:
        action = str(fields.get("action") or "").strip().upper()
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
            seconds = float(fields.get("wait", 0.0))
            return {
                "type": "PAUSE",
                "status": "pending",
                "details": f"Pause for {seconds:.1f} sec",
                "pause_seconds": seconds,
            }
        if action == "ALERT":
            msg = str(fields.get("alert") or "").strip()
            if not msg:
                raise ValueError("Alert message cannot be empty.")
            return {
                "type": "ALERT",
                "status": "pending",
                "details": f"Alert: {msg}",
                "alert_message": msg,
            }

        params: dict = {}
        if action == "COMMAND":
            cmd = str(fields.get("cmd") or "").strip()
            if not cmd:
                raise ValueError("Raw cmd cannot be empty for COMMAND action.")
            params = {"cmd": cmd}
        elif action == "APPLY":
            params = {
                "units": str(fields.get("units")),
                "mode": str(fields.get("mode")),
                "diameter_mm": float(fields.get("diameter_mm")),
                "rate": float(fields.get("rate")),
                "volume": float(fields.get("volume")),
            }
        elif action == "HEXW2":
            params = {
                "units": str(fields.get("units")),
                "mode": str(fields.get("mode")),
                "diameter_mm": float(fields.get("diameter_mm")),
                "volume": float(fields.get("volume")),
                "rate": float(fields.get("rate")),
                "delay_min": float(fields.get("delay_min", 0.0)),
                "start": True,
                "target_eta_s": float(fields.get("target_eta_s", FLOWCELL_FILL_TARGET_S)),
                "track_collection": bool(fields.get("track_collection", False)),
                "collection_capacity_ml": float(fields.get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML)),
                "collection_warn_ml": float(fields.get("collection_warn_ml", default_collection_warn_ml(fields.get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML)))),
            }
        elif action in {"START", "PAUSE", "STOP", "RESTART", "STATUS", "STATUS_PORT", "STATE_RESET"}:
            params = {}
        else:
            raise ValueError(f"Unsupported pump action: {action}")

        return {
            "type": f"PUMP_{action}",
            "status": "pending",
            "details": build_pump_details(action, params),
            "pump_action": {"name": action, "params": params},
        }

    def _edit_queue_item(self, index: int):
        if self._session.is_running:
            messagebox.showwarning("Queue Running", "Stop before editing.")
            return
        if index < 0 or index >= len(self._session.measurement_queue):
            return
        item = self._session.measurement_queue[index]
        if not self._is_editable_item(item):
            return

        fields = self._extract_edit_fields(item)
        win = tk.Toplevel(self._frame)
        win.title("Edit Queue Step")
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

        ttk.Label(win, text="Diameter (mm):").grid(row=0, column=6, **pad, sticky="e")
        diameter_var = tk.DoubleVar(value=fields["diameter_mm"])
        ttk.Entry(win, width=10, textvariable=diameter_var).grid(row=0, column=7, **pad, sticky="w")

        ttk.Label(win, text="Syringe preset:").grid(row=1, column=0, **pad, sticky="e")
        syringe_var = tk.StringVar(value="Custom")
        ttk.Combobox(
            win,
            textvariable=syringe_var,
            values=["Custom"] + sorted(SYRINGE_PRESETS_MM.keys()),
            width=22,
            state="readonly",
        ).grid(row=1, column=1, columnspan=2, **pad, sticky="w")

        def _apply_preset(_e=None):
            key = (syringe_var.get() or "").strip()
            if not key or key == "Custom":
                return
            mm = SYRINGE_PRESETS_MM.get(key)
            if mm is None:
                return
            diameter_var.set(float(mm))

        ttk.Label(win, text="Rate:").grid(row=2, column=0, **pad, sticky="e")
        rate_var = tk.DoubleVar(value=fields["rate"])
        ttk.Entry(win, width=10, textvariable=rate_var).grid(row=2, column=1, **pad, sticky="w")

        ttk.Label(win, text="Volume:").grid(row=2, column=2, **pad, sticky="e")
        volume_var = tk.DoubleVar(value=fields["volume"])
        ttk.Entry(win, width=10, textvariable=volume_var).grid(row=2, column=3, **pad, sticky="w")

        ttk.Label(win, text="Delay (min):").grid(row=2, column=4, **pad, sticky="e")
        delay_var = tk.DoubleVar(value=fields["delay_min"])
        ttk.Entry(win, width=10, textvariable=delay_var).grid(row=2, column=5, **pad, sticky="w")

        ttk.Label(win, text="Wait (sec):").grid(row=2, column=6, **pad, sticky="e")
        wait_var = tk.DoubleVar(value=fields["wait"])
        ttk.Entry(win, width=10, textvariable=wait_var).grid(row=2, column=7, **pad, sticky="w")

        ttk.Label(win, text="Target ETA (s):").grid(row=3, column=0, **pad, sticky="e")
        target_eta_var = tk.DoubleVar(value=fields["target_eta_s"])
        ttk.Entry(win, width=10, textvariable=target_eta_var).grid(row=3, column=1, **pad, sticky="w")

        def _match_eta():
            rate = rate_for_target_eta(volume_var.get(), units_var.get(), target_eta_var.get())
            if rate is None:
                messagebox.showerror("Invalid ETA", "Provide volume, units, and a positive target ETA.")
                return
            rate_var.set(float(rate))
            _update_eta_label()

        def _apply_flowcell_preset():
            action_var.set("HEXW2")
            units_var.set("uLmin")
            mode_var.set("withdraw")
            volume_var.set(float(FLOWCELL_FILL_VOLUME_UL))
            target_eta_var.set(float(FLOWCELL_FILL_TARGET_S))
            rate = rate_for_target_eta(FLOWCELL_FILL_VOLUME_UL, "uLmin", FLOWCELL_FILL_TARGET_S)
            if rate is not None:
                rate_var.set(float(rate))
            track_collection_var.set(True)
            capacity_var.set(float(COLLECTION_SYRINGE_CAPACITY_ML))
            warn_var.set(float(default_collection_warn_ml(capacity_var.get())))
            try:
                syringe_var.set("50/60 mL (typical)")
                diameter_var.set(float(SYRINGE_PRESETS_MM["50/60 mL (typical)"]))
            except Exception:
                pass
            _update_eta_label()

        ttk.Button(win, text="Match ETA", command=_match_eta).grid(row=3, column=2, **pad, sticky="w")
        ttk.Button(win, text="Preset Flowcell Pull", command=_apply_flowcell_preset).grid(
            row=3, column=3, columnspan=2, **pad, sticky="w"
        )

        track_collection_var = tk.BooleanVar(value=fields["track_collection"])
        ttk.Checkbutton(win, text="Track collected volume", variable=track_collection_var).grid(
            row=3, column=5, columnspan=2, padx=6, pady=4, sticky="w"
        )

        ttk.Label(win, text="Capacity (mL):").grid(row=4, column=0, **pad, sticky="e")
        capacity_var = tk.DoubleVar(value=fields["collection_capacity_ml"])
        ttk.Entry(win, width=10, textvariable=capacity_var).grid(row=4, column=1, **pad, sticky="w")

        ttk.Label(win, text="Warn at (mL):").grid(row=4, column=2, **pad, sticky="e")
        warn_var = tk.DoubleVar(value=fields["collection_warn_ml"])
        ttk.Entry(win, width=10, textvariable=warn_var).grid(row=4, column=3, **pad, sticky="w")

        eta_label = ttk.Label(win, text="ETA: -", foreground="#555")
        eta_label.grid(row=4, column=4, columnspan=4, padx=6, pady=4, sticky="w")

        def _update_eta_label(*_args):
            eta_s = estimate_eta_seconds(volume_var.get(), rate_var.get(), units_var.get())
            if eta_s is None:
                eta_label.configure(text="ETA: -")
                return
            extra = ""
            if bool(track_collection_var.get()):
                volume_ul = volume_to_ul(volume_var.get(), units_var.get())
                if volume_ul is not None:
                    extra = f" | collect {volume_ul / 1000.0:.3f} mL"
            eta_label.configure(text=f"ETA: {eta_s:.1f}s{extra}")

        for var in (units_var, rate_var, volume_var, target_eta_var, track_collection_var):
            try:
                var.trace_add("write", _update_eta_label)
            except Exception:
                pass
        syringe_var.trace_add("write", lambda *_: _apply_preset())
        _update_eta_label()

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
                new_item = self._build_edited_queue_item(
                    {
                        "action": action_var.get(),
                        "units": units_var.get(),
                        "mode": mode_var.get(),
                        "diameter_mm": diameter_var.get(),
                        "rate": rate_var.get(),
                        "volume": volume_var.get(),
                        "delay_min": delay_var.get(),
                        "cmd": (cmd_var.get() or "").strip(),
                        "wait": wait_var.get(),
                        "alert": (alert_var.get() or "").strip(),
                        "target_eta_s": target_eta_var.get(),
                        "track_collection": track_collection_var.get(),
                        "collection_capacity_ml": capacity_var.get(),
                        "collection_warn_ml": warn_var.get(),
                    }
                )
            except Exception as exc:
                messagebox.showerror("Invalid queue step", str(exc))
                return

            if "method_ref" in item and "method_ref" not in new_item:
                new_item["method_ref"] = dict(item.get("method_ref") or {})
            new_item["status"] = item.get("status", "pending")
            self._session.measurement_queue[index] = new_item
            self.refresh()
            win.destroy()

        ttk.Button(btns, text="Update", command=_apply).pack(side="left", padx=6)
        ttk.Button(btns, text="Cancel", command=win.destroy).pack(side="left", padx=6)
        win.bind("<Return>", lambda _e: _apply())
        win.bind("<Escape>", lambda _e: win.destroy())

    # ── Drag reorder ──────────────────────────────────────────────────────────

    def _drag_start(self, event):
        if self._session.is_running:
            return
        item = self._tree.identify_row(event.y)
        if item:
            self._last_selected = item
            self._drag_item = item
            if not self._reorder_pending:
                self._reorder_snapshot = list(self._session.measurement_queue)

    def _drag_motion(self, event):
        if self._session.is_running or not self._drag_item:
            return
        target = self._tree.identify_row(event.y)
        if target and target != self._drag_item:
            self._tree.move(self._drag_item, "", self._tree.index(target))
            self._reorder_pending = True

    def _drag_release(self, event):
        if self._reorder_pending:
            self.set_status("Queue reorder pending — click ✓ Confirm Move")
        self._drag_item = None

    def confirm_reorder(self):
        if not self._reorder_pending or not self._reorder_snapshot:
            messagebox.showinfo("No Changes", "No pending reorder.")
            return
        try:
            order = [int(iid) for iid in self._tree.get_children()]
        except Exception:
            messagebox.showerror("Reorder Error", "Failed to read queue order.")
            return
        if any(i < 0 or i >= len(self._reorder_snapshot) for i in order):
            messagebox.showerror("Reorder Error", "Queue order out of range.")
            return
        self._session.measurement_queue = [self._reorder_snapshot[i] for i in order]
        self._reorder_snapshot = None
        self._reorder_pending  = False
        self.refresh()
        self.set_status("Queue reordered")

    def _reset_reorder(self):
        if self._reorder_pending:
            self._reorder_pending  = False
            self._reorder_snapshot = None
            self.refresh()

    # ── Save / Load ───────────────────────────────────────────────────────────

    def save_queue(self):
        if not self._session.measurement_queue:
            messagebox.showwarning("Empty Queue", "Nothing to save."); return
        if self._session.is_running:
            messagebox.showwarning("Running", "Stop the queue first."); return
        path = filedialog.asksaveasfilename(
            title="Save Queue",
            defaultextension=".json",
            filetypes=(("Queue Files", "*.json"), ("All", "*.*")),
            initialfile=f"queue_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        if not path:
            return
        payload = {
            "metadata": {"saved_at": datetime.now().isoformat(timespec="seconds"),
                         "version": 1},
            "items": [self._serialize(i) for i in self._session.measurement_queue],
        }
        try:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            messagebox.showinfo("Saved", f"Queue saved to:\n{path}")
            self.log(f"Queue saved: {path}")
            self._last_queue_path = path
        except OSError as exc:
            messagebox.showerror("Save Failed", str(exc))

    def load_queue(self):
        if self._session.is_running:
            messagebox.showwarning("Running", "Stop the queue first."); return
        path = filedialog.askopenfilename(
            title="Load Queue",
            defaultextension=".json",
            filetypes=(("Queue Files", "*.json"), ("All", "*.*")),
        )
        if not path:
            return
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                payload = json.load(fh)
            if isinstance(payload, list):
                items = payload
            else:
                items = payload.get("items")
            if not isinstance(items, list):
                raise ValueError("Queue file missing 'items' list")
        except Exception as exc:
            messagebox.showerror("Load Failed", str(exc)); return

        new_queue, skipped = [], 0
        for raw in items:
            item = self._deserialize(raw)
            if item is None:
                skipped += 1
            else:
                new_queue.append(item)

        if not new_queue:
            messagebox.showwarning("Load Queue", "No valid items found."); return

        self._session.measurement_queue = new_queue
        self.refresh()
        self.set_status(f"Queue loaded ({len(new_queue)} items)")
        self.log(f"Queue loaded: {path} ({len(new_queue)} items)")
        self._last_queue_path = path
        if skipped:
            self.log(f"Queue load skipped {skipped} invalid item(s).")
        messagebox.showinfo("Queue Loaded", f"Loaded {len(new_queue)} item(s).")

    @staticmethod
    def _serialize(item: dict) -> dict:
        data = {k: item.get(k) for k in ("type", "status", "details")}
        t = data["type"]
        if t == "PAUSE":
            data["pause_seconds"] = item.get("pause_seconds", 0.0)
        elif t == "ALERT":
            data["alert_message"] = item.get("alert_message", "")
        elif t and t.startswith("PUMP_"):
            action = item.get("pump_action") or {}
            data["pump_action"] = {"name": action.get("name"),
                                   "params": dict(action.get("params") or {})}
        elif t and t.startswith("OPENTRONS_"):
            action = item.get("opentrons_action") or {}
            data["opentrons_action"] = {"name": action.get("name"),
                                        "params": dict(action.get("params") or {})}
        elif t and t.startswith("MISC_"):
            action = item.get("misc_action") or {}
            data["misc_action"] = {"name": action.get("name"),
                                   "params": dict(action.get("params") or {})}
        else:
            if "script_path" in item:
                data["script_path"] = item["script_path"]
            if "method_ref" in item:
                data["method_ref"] = dict(item.get("method_ref") or {})
        return data

    def _deserialize(self, raw: dict):
        if not isinstance(raw, dict):
            return None
        t = raw.get("type")
        if not t:
            return None
        item = {"type": t, "status": "pending"}
        details = raw.get("details")
        if t == "PAUSE":
            try:
                item["pause_seconds"] = float(raw.get("pause_seconds", 0.0))
            except (TypeError, ValueError):
                return None
            item["details"] = details or f"Pause for {item['pause_seconds']:.1f} sec"
        elif t == "ALERT":
            msg = raw.get("alert_message")
            if not isinstance(msg, str) or not msg.strip():
                return None
            item["alert_message"] = msg.strip()
            item["details"]       = details or "Alert pause"
        elif t.startswith("PUMP_"):
            action = raw.get("pump_action") or {}
            if not action.get("name"):
                return None
            item["pump_action"] = {"name": action["name"],
                                   "params": dict(action.get("params") or {})}
            item["details"] = details or f"Pump action {action['name']}"
        elif t.startswith("OPENTRONS_"):
            action = raw.get("opentrons_action") or {}
            params = dict(action.get("params") or {})
            action_name = str(action.get("name") or "").strip().upper()
            if not action_name:
                return None
            protocol_label = str(params.get("protocol_name") or "").strip()
            if action_name == "RESUME":
                resume_key = str(params.get("resume_key") or "").strip()
                if not resume_key:
                    return None
                protocol_label = protocol_label or "Opentrons protocol"
                item["opentrons_action"] = {"name": action_name, "params": params}
                item["details"] = details or f"Opentrons RESUME {protocol_label}"
            elif action_name == "HOME":
                host = str(params.get("robot_host") or "").strip()
                if not host:
                    return None
                try:
                    params["robot_port"] = int(params.get("robot_port") or 31950)
                except Exception:
                    return None
                item["opentrons_action"] = {"name": action_name, "params": params}
                item["details"] = details or f"Opentrons HOME {host}"
            elif action_name == "PROTOCOL":
                protocol_path = params.get("protocol_path")
                protocol_source = params.get("protocol_source")
                if not protocol_path and not protocol_source:
                    return None
                mode = str(params.get("mode") or "validate").lower()
                if protocol_path:
                    resolved = self._resolve_opentrons_protocol_path(protocol_path)
                    if resolved is None:
                        return None
                    params["protocol_path"] = str(resolved)
                    protocol_label = protocol_label or resolved.name
                else:
                    protocol_label = protocol_label or "inline protocol"
                if mode == "robot":
                    params["robot_host"] = str(params.get("robot_host") or OPENTRONS_DEFAULT_HOST).strip()
                    try:
                        params["robot_port"] = int(params.get("robot_port") or OPENTRONS_DEFAULT_API_PORT)
                    except Exception:
                        return None
                item["opentrons_action"] = {"name": action_name, "params": params}
                item["details"] = details or f"Opentrons {mode.upper()} {protocol_label}"
            else:
                return None
        elif t.startswith("MISC_"):
            action = raw.get("misc_action") or {}
            params = dict(action.get("params") or {})
            action_name = str(action.get("name") or "").strip().upper()
            if action_name != "COMPRESS_SEND":
                return None
            mode = str(params.get("folder_mode") or "current_experiment").strip().lower()
            if mode not in {"current_experiment", "specific_folder"}:
                return None
            if mode == "specific_folder":
                folder_path = str(params.get("folder_path") or "").strip()
                if not folder_path:
                    return None
                params["folder_path"] = folder_path
            item["misc_action"] = {"name": action_name, "params": params}
            item["details"] = details or (
                f"Compress + send folder: {params.get('folder_path')}"
                if mode == "specific_folder"
                else "Compress + send current experiment folder"
            )
        else:
            sp = raw.get("script_path")
            method_ref = raw.get("method_ref") or {}

            if sp:
                # Prefer exact library_map entry when provided.
                hash_key = method_ref.get("hash_key")
                if hash_key:
                    resolved = library_map.lookup(hash_key)
                    if resolved is not None:
                        sp = str(resolved)

                # Prefer MUX-specific library file if method_ref requests a channel.
                mux = method_ref.get("mux_channel")
                mux_ch = None
                if mux not in (None, "", 0, "0"):
                    try:
                        mux_ch = int(mux)
                    except (TypeError, ValueError):
                        mux_ch = None

                if mux_ch is not None and 1 <= mux_ch <= 16:
                    technique = method_ref.get("technique") or t
                    params = method_ref.get("params")
                    resolved = None
                    if isinstance(params, dict):
                        try:
                            mux_key = library_map.compute_hash(technique, params, mux_ch)
                            resolved = library_map.lookup(mux_key)
                        except Exception:
                            resolved = None
                    if resolved is not None:
                        sp = str(resolved)
                        item["details"] = details or f"{Path(sp).name} (MUX ch {mux_ch})"

            if not sp:
                hash_key = method_ref.get("hash_key")
                if hash_key:
                    path = library_map.lookup(hash_key)
                    if path is None:
                        return None
                    mux = method_ref.get("mux_channel")
                    if mux not in (None, "", 0, "0"):
                        try:
                            mux_ch = int(mux)
                        except (TypeError, ValueError):
                            mux_ch = None

                        if mux_ch is not None and 1 <= mux_ch <= 16:
                            technique = method_ref.get("technique") or t
                            params = method_ref.get("params")
                            resolved = None

                            if isinstance(params, dict):
                                try:
                                    mux_key = library_map.compute_hash(technique, params, mux_ch)
                                    resolved = library_map.lookup(mux_key)
                                except Exception:
                                    resolved = None

                            if resolved is None:
                                # Fallback: wrap the referenced base script with the requested channel.
                                try:
                                    base_script = path.read_text(encoding="utf-8")
                                    wrapped = self._wrap_mux(
                                        self._strip_first_mux_header(base_script),
                                        mux_ch,
                                    )
                                    mux_note = self._compose_mux_note(
                                        method_ref=method_ref,
                                        mux_channel=mux_ch,
                                        fallback=f"MUX ch {mux_ch}",
                                    )
                                    saved_path, _ = self._session.registry.save_script(
                                        technique=technique,
                                        script=wrapped,
                                        params=params if isinstance(params, dict) else None,
                                        mux_channel=mux_ch,
                                        note=mux_note,
                                    )
                                    resolved = saved_path
                                except Exception as exc:
                                    self.log(f"Failed to generate MUX ch {mux_ch} script from method_ref: {exc}")
                                    return None

                            sp = str(resolved)
                            item["details"] = details or f"{Path(sp).name} (MUX ch {mux_ch})"
                        else:
                            sp = str(path)
                            item["details"] = details or path.name
                    else:
                        sp = str(path)
                        item["details"] = details or path.name
                else:
                    return None

            item["script_path"] = sp
            if "method_ref" in raw and isinstance(raw.get("method_ref"), dict):
                item["method_ref"] = dict(raw.get("method_ref") or {})
            item["details"]     = item.get("details") or details or Path(sp).name
        return item

    @staticmethod
    def _resolve_opentrons_protocol_path(protocol_path: str | Path) -> Optional[Path]:
        raw = Path(protocol_path).expanduser()
        candidates: list[Path] = []
        if raw.is_absolute():
            candidates.append(raw)
        else:
            candidates.append(Path.cwd() / raw)
            candidates.append(Path(OPENTRONS_PROTOCOLS_DIR) / raw)
            candidates.append(Path(OPENTRONS_PROTOCOLS_DIR) / raw.name)
        for candidate in candidates:
            try:
                resolved = candidate.resolve()
            except Exception:
                resolved = candidate
            if resolved.exists():
                return resolved
        proto_root = Path(OPENTRONS_PROTOCOLS_DIR)
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

    @staticmethod
    def _mux_channel_address(channel: int) -> int:
        idx = channel - 1
        return (idx << 4) | idx

    @classmethod
    def _wrap_mux(cls, base_script: str, channel: int) -> str:
        lines = base_script.splitlines()
        header = lines[0].strip() if lines and lines[0].strip() in ("e", "l") else "e"
        rest = lines[1:] if lines and lines[0].strip() in ("e", "l") else lines
        addr = cls._mux_channel_address(channel)
        prefix = [
            header,
            "# MUX16 channel select",
            "set_gpio_cfg 0x3FFi 1",
            f"set_gpio {addr}i",
        ]
        return "\n".join(prefix + rest)

    @staticmethod
    def _strip_first_mux_header(script: str) -> str:
        lines = script.splitlines()
        cfg_idx = None
        gpio_idx = None
        for i, line in enumerate(lines):
            s = line.strip()
            if cfg_idx is None and s == "set_gpio_cfg 0x3FFi 1":
                cfg_idx = i
                continue
            if cfg_idx is not None and gpio_idx is None and s.startswith("set_gpio ") and not s.startswith("set_gpio_cfg"):
                gpio_idx = i
                break
        if cfg_idx is not None and gpio_idx is not None:
            del lines[gpio_idx]
            del lines[cfg_idx]
        return "\n".join(lines)

    @staticmethod
    def _extract_mux_from_script(script: str) -> Optional[int]:
        """Read first set_gpio value and decode nibble-pair channel (0x11 -> ch2)."""
        for line in script.splitlines():
            s = line.strip()
            if not s.startswith("set_gpio ") or s.startswith("set_gpio_cfg"):
                continue
            token = s[len("set_gpio "):].strip()
            if token.endswith("i"):
                token = token[:-1]
            try:
                value = int(token, 16) if token.lower().startswith("0x") else int(token)
            except ValueError:
                continue
            lo = value & 0x0F
            hi = (value >> 4) & 0x0F
            if lo == hi and 0 <= lo <= 15:
                return lo + 1
            return None
        return None

    def _compose_mux_note(self, method_ref: dict, mux_channel: int, fallback: str) -> str:
        """Build note using original method note (if any) + current channel tag."""
        base_note = ""
        if isinstance(method_ref, dict):
            hash_key = method_ref.get("hash_key")
            if hash_key:
                try:
                    entry = library_map.all_entries().get(hash_key) or {}
                    base_note = (entry.get("note") or "").strip()
                except Exception:
                    base_note = ""

        tag = f"MUX ch {mux_channel}"
        if base_note:
            if re.search(r"\bMUX\s*ch\s*\d+\b", base_note, flags=re.IGNORECASE):
                return re.sub(
                    r"\bMUX\s*ch\s*\d+\b",
                    tag,
                    base_note,
                    flags=re.IGNORECASE,
                )
            return f"{base_note} | {tag}"
        return fallback

    # ── Run queue ─────────────────────────────────────────────────────────────

    def run_queue(self):
        self._reset_reorder()
        if not self._session.measurement_queue:
            messagebox.showwarning("Empty Queue", "No items in queue."); return
        if self._session.is_running:
            messagebox.showwarning("Already Running", "Queue already running."); return
        if not self._has_motion_step(start_index=0):
            messagebox.showwarning(
                "No Motion Step",
                "Queue contains no motion/measurement step. "
                "Pump APPLY only sets parameters and does not move liquid.",
            )
        self._session.is_running = True
        self._session.update_queue_status(
            state="running",
            current_index=0,
            total=len(self._session.measurement_queue),
            current_label="(starting)",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.refresh_labels()
        self.clear_log()
        self.log("Queue start requested.")
        self.log(f"Measurement simulation: {'ON' if self._session.simulate_measurements else 'OFF'}")
        self.log(
            "Loaded syringe state: "
            f"{self._session.collection_steps} step(s), "
            f"{self._session.collection_volume_ul / 1000.0:.3f} / "
            f"{self._session.collection_capacity_ul / 1000.0:.1f} mL"
        )
        self._announce_queue_start(start_index=0)
        self._copy_queue_file_async("run_queue")
        self._queue_thread = threading.Thread(
            target=self._execute_queue, args=(0,), daemon=True
        )
        self._queue_thread.start()

    def run_from_selected(self):
        self._reset_reorder()
        if not self._session.measurement_queue:
            messagebox.showwarning("Empty Queue", "No items in queue."); return
        if self._session.is_running:
            messagebox.showwarning("Already Running", "Queue already running."); return
        sel = self._tree.selection()
        if not sel:
            messagebox.showwarning("No Selection", "Select a queue item to start from.")
            return
        try:
            idx = self._tree.index(sel[0])
        except Exception:
            messagebox.showerror("Selection Error", "Could not determine selected item.")
            return
        if not self._has_motion_step(start_index=idx):
            messagebox.showwarning(
                "No Motion Step",
                "Selected range has no motion/measurement step. "
                "Pump APPLY only sets parameters and does not move liquid.",
            )
        self._session.is_running = True
        self._session.update_queue_status(
            state="running",
            current_index=0,
            total=len(self._session.measurement_queue) - idx,
            current_label="(starting)",
            started_at=datetime.now().isoformat(timespec="seconds"),
        )
        self.refresh_labels()
        self.clear_log()
        self.log("Queue start from selected requested.")
        self.log(f"Measurement simulation: {'ON' if self._session.simulate_measurements else 'OFF'}")
        self.log(
            "Loaded syringe state: "
            f"{self._session.collection_steps} step(s), "
            f"{self._session.collection_volume_ul / 1000.0:.3f} / "
            f"{self._session.collection_capacity_ul / 1000.0:.1f} mL"
        )
        self._announce_queue_start(start_index=idx)
        self._copy_queue_file_async("run_queue_from_selected")
        self._queue_thread = threading.Thread(
            target=self._execute_queue, args=(idx,), daemon=True
        )
        self._queue_thread.start()

    def _has_motion_step(self, start_index: int) -> bool:
        items = self._session.measurement_queue[start_index:]
        for item in items:
            t = str(item.get("type") or "").upper()
            if not t:
                continue
            if t in {"PAUSE", "ALERT"}:
                continue
            if t.startswith("PUMP_"):
                action = str((item.get("pump_action") or {}).get("name") or "").upper()
                if action in {"HEXW2", "START"}:
                    return True
                continue
            return True
        return False

    def stop_queue(self):
        queue_was_running = bool(self._session.is_running)
        self.log("Queue stop requested.")
        self._session.is_running = False
        self._session.stop_current_runner()
        self._session.update_queue_status(state="stopping")
        self.set_status("Queue Stopping")

        # Always try to force pump out of motion on stop. Run in background to
        # keep the UI responsive if serial calls take up to timeout.
        threading.Thread(target=self._force_stop_and_restart_pump, daemon=True).start()
        threading.Thread(target=self._stop_and_home_opentrons, daemon=True).start()

        if not queue_was_running:
            self._session.update_queue_status(state="stopped")
            self.set_status("Queue Stopped")

    def _force_stop_and_restart_pump(self) -> None:
        ctrl = self._pump_ctrl
        if ctrl is None:
            self.log("Queue stop: pump backend unavailable.")
            return
        if not getattr(ctrl, "connected", False):
            self.log("Queue stop: pump not connected.")
            return
        try:
            try:
                prep = ctrl.status_port()
                self._log_pump_status("status port (stop)", prep)
            except Exception:
                pass
            try:
                resp = ctrl.stop()
                if resp:
                    self.log(f"Pump <- {resp}")
            except Exception as exc:
                self.log(f"Queue stop: pump stop failed: {exc}")
            try:
                resp = ctrl.restart()
                if resp:
                    self.log(f"Pump <- {resp}")
                self.log("Queue stop: pump restart sent.")
            except Exception as exc:
                self.log(f"Queue stop: pump restart failed: {exc}")
        finally:
            self._session.update_queue_status(state="stopped")
            self._root.after(0, self.set_status, "Queue Stopped")

    def _stop_and_home_opentrons(self) -> None:
        targets: list[tuple[str, int, str | None]] = []
        seen: set[tuple[str, int, str | None]] = set()

        active = self._active_opentrons_target or {}
        active_host = str(active.get("robot_host") or "").strip()
        active_run_id = str(active.get("run_id") or "").strip() or None
        try:
            active_port = int(active.get("robot_port") or 31950)
        except Exception:
            active_port = 31950
        if active_host:
            key = (active_host, active_port, active_run_id)
            seen.add(key)
            targets.append(key)

        for paused in self._opentrons_paused_runs.values():
            host = str(paused.get("robot_host") or "").strip()
            run_id = str(paused.get("run_id") or "").strip() or None
            if not host:
                continue
            try:
                port = int(paused.get("robot_port") or 31950)
            except Exception:
                port = 31950
            key = (host, port, run_id)
            if key in seen:
                continue
            seen.add(key)
            targets.append(key)

        if not targets:
            return

        runner = OpentronsProtocolRunner(log_callback=self.log)
        for host, port, run_id in targets:
            if run_id:
                runner.stop_run(robot_host=host, robot_port=port, run_id=run_id)
                time.sleep(1.0)
            else:
                runner.stop_active_runs(robot_host=host, robot_port=port)
                time.sleep(1.0)
            runner.home_robot(robot_host=host, robot_port=port)

        self._opentrons_paused_runs.clear()
        self._active_opentrons_target = None

    def _execute_queue(self, start_index: int = 0):
        queue = list(self._session.measurement_queue)
        for i, item in enumerate(queue[start_index:], start=start_index):
            if not self._session.is_running:
                self.log("Queue execution stopped by user."); break

            self._session.measurement_queue[i]["status"] = "running"
            self._root.after(0, self.refresh)
            self._root.after(0, self.set_status,
                             f"Running: {item['type']} — {item.get('details', '')}")
            self._session.update_queue_status(
                state="running",
                current_index=(i - start_index + 1),
                total=len(queue) - start_index,
                current_label=(item.get("details") or item.get("type") or ""),
            )
            self.log(f"Queue start -> {item.get('details', item.get('type'))}")

            csv_path = None
            success  = False
            try:
                t = item["type"]
                if t == "PAUSE":
                    ok = self._exec_pause(float(item.get("pause_seconds", 0)))
                    self._session.measurement_queue[i]["status"] = "completed" if ok else "stopped"
                    success = ok

                elif t == "ALERT":
                    alert_msg = item.get("alert_message", "Paused — click OK.")
                    session_mgr = getattr(self._session, "session_manager", None)
                    if session_mgr is not None:
                        session_mgr.notify_slack(f"Queue alert: {alert_msg}")
                    ok = self._exec_alert(alert_msg)
                    self._session.measurement_queue[i]["status"] = "completed" if ok else "stopped"
                    success = ok

                elif t.startswith("PUMP_"):
                    action_name = str((item.get("pump_action") or {}).get("name") or "").upper()
                    if action_name == "STATE_RESET":
                        self._session.reset_collection_tracking(reason="queue state reset step")
                        self.log("Syringe state reset to 0 mL.")
                        self._root.after(0, self.refresh_labels)
                        ok = True
                    else:
                        ok = self._exec_pump(item)
                    self._session.measurement_queue[i]["status"] = "completed" if ok else "failed"
                    success = ok

                elif t.startswith("OPENTRONS_"):
                    outcome = self._exec_opentrons(item, queue_index=i)
                    if outcome == "paused":
                        self._session.measurement_queue[i]["status"] = "paused"
                        success = True
                    else:
                        ok = outcome == "completed"
                        self._session.measurement_queue[i]["status"] = (
                            "completed" if ok else ("stopped" if not self._session.is_running else "failed")
                        )
                        success = ok

                elif t.startswith("MISC_"):
                    ok = self._exec_misc(item)
                    self._session.measurement_queue[i]["status"] = "completed" if ok else "failed"
                    success = ok

                else:
                    self._ensure_mux_script_for_item(item)
                    self._root.after(0, self._plotter.start_live,
                                     f"{item['type']} (live)", None, item["type"])
                    try:
                        mux_channel = self._extract_mux_channel(item)
                        meas_tag = self._session.next_meas_tag_with_mux(mux_channel)
                        self.log(f"[Tag] {meas_tag}")
                        self._root.after(0, self.refresh_labels)
                        data_folder = None
                        if self._session.session_manager is not None:
                            data_folder = self._session.session_manager.require_experiment()
                            if data_folder is None:
                                self._session.measurement_queue[i]["status"] = "failed"
                                self._root.after(0, self.refresh)
                                break
                        runner = SerialMeasurementRunner(
                            Path(item["script_path"]),
                            log_callback=self.log,
                            data_callback=self._plotter.push_live_point,
                            data_folder=data_folder,
                            save_raw_packets=self._session.save_raw_packets,
                            simulate_measurements=self._session.simulate_measurements,
                            invert_current=(item.get("type") == "SWV"),
                            pump_com_port=CHEMYX_DEFAULT_PORT,
                            preferred_port=self._session.device_port,
                        )
                        self._session.current_runner = runner
                        success, csv_path = runner.execute(meas_tag=meas_tag)
                        self._session.measurement_queue[i]["status"] = (
                            "completed" if success else "failed"
                        )
                    finally:
                        self._session.current_runner = None
                        self._root.after(0, self._plotter.stop_live)

            except Exception as exc:
                self._session.measurement_queue[i]["status"] = "failed"
                self.log(f"CRITICAL ERROR in queue: {exc}")

            if csv_path:
                self._root.after(0, self._plotter.plot_data, csv_path,
                                 self._session.last_live_plot_color, None, True, False)
            self._root.after(0, self.refresh)
            step_delay = getattr(self._session, "step_delay", 0.0) or 0.0
            if step_delay > 0 and i < len(queue) - 1:
                if not self._exec_pause(step_delay):
                    break

        self._session.is_running = False
        self.log("Queue completed.")
        self._root.after(0, self.set_status, "Queue Complete")
        self._announce_queue_end(start_index=start_index)

    def _announce_queue_start(self, start_index: int):
        session_mgr = getattr(self._session, "session_manager", None)
        if session_mgr is None:
            return
        total = max(0, len(self._session.measurement_queue) - start_index)
        session_name = (
            session_mgr.current_session_path.name
            if session_mgr.current_session_path is not None
            else "(none)"
        )
        experiment_name = (
            session_mgr.current_experiment_path.name
            if session_mgr.current_experiment_path is not None
            else "(none)"
        )
        msg = (
            f"Queue started: {total} item(s). "
            f"Session={session_name}; Experiment={experiment_name}."
        )
        try:
            session_mgr.notify_slack(msg)
        except Exception:
            try:
                session_mgr.log(msg)
            except Exception:
                self.log(msg)

    def _announce_queue_end(self, start_index: int):
        session_mgr = getattr(self._session, "session_manager", None)
        if session_mgr is None:
            return

        ran = self._session.measurement_queue[start_index:]
        if not ran:
            return

        total = len(ran)
        completed = sum(1 for item in ran if item.get("status") == "completed")
        failed = sum(1 for item in ran if item.get("status") == "failed")
        stopped = sum(1 for item in ran if item.get("status") == "stopped")
        paused = sum(1 for item in ran if item.get("status") == "paused")

        if stopped > 0:
            state = "STOPPED"
        elif paused > 0:
            state = "PAUSED"
        elif failed > 0:
            state = "FAILED"
        else:
            state = "COMPLETED"

        self._session.update_queue_status(
            state=state.lower(),
            current_index=total,
            total=total,
            current_label="(finished)",
        )

        session_name = (
            session_mgr.current_session_path.name
            if session_mgr.current_session_path is not None
            else "(none)"
        )
        experiment_name = (
            session_mgr.current_experiment_path.name
            if session_mgr.current_experiment_path is not None
            else "(none)"
        )
        msg = (
            f"Queue {state}: completed={completed}/{total}, "
            f"failed={failed}, stopped={stopped}, paused={paused}. "
            f"Session={session_name}; Experiment={experiment_name}."
        )
        try:
            session_mgr.notify_slack(msg)
        except Exception:
            try:
                session_mgr.log(msg)
            except Exception:
                self.log(msg)

    def _ensure_mux_script_for_item(self, item: dict):
        """Auto-correct script_path to requested MUX channel before execution."""
        mux_channel = self._extract_mux_channel(item)
        if mux_channel is None:
            return
        script_path = item.get("script_path")
        if not script_path:
            return

        src = Path(script_path)
        try:
            base_script = src.read_text(encoding="utf-8")
        except Exception as exc:
            self.log(f"Warning: could not read script for MUX verification: {exc}")
            return

        current_mux = self._extract_mux_from_script(base_script)
        if current_mux == mux_channel:
            return

        wrapped = self._wrap_mux(self._strip_first_mux_header(base_script), mux_channel)
        method_ref = item.get("method_ref") or {}
        params = method_ref.get("params")
        try:
            mux_note = self._compose_mux_note(
                method_ref=method_ref,
                mux_channel=mux_channel,
                fallback=f"MUX ch {mux_channel}",
            )
            saved_path, saved_name = self._session.registry.save_script(
                technique=item.get("type", ""),
                script=wrapped,
                params=params if isinstance(params, dict) else None,
                mux_channel=mux_channel,
                note=mux_note,
            )
            item["script_path"] = str(saved_path)
            self.log(
                f"Adjusted script for MUX ch {mux_channel}: {src.name} -> {saved_name}"
            )
        except Exception as exc:
            self.log(f"Warning: failed to adjust script for MUX ch {mux_channel}: {exc}")

    def _queue_payload(self, items: list[dict] | None = None) -> dict:
        queue_items = items if items is not None else self._session.measurement_queue
        return {
            "metadata": {"saved_at": datetime.now().isoformat(timespec="seconds"),
                         "version": 1},
            "items": [self._serialize(i) for i in queue_items],
        }

    def _copy_queue_file(self, prefix: str):
        session_mgr = getattr(self._session, "session_manager", None)
        exp_path = getattr(session_mgr, "current_experiment_path", None) if session_mgr else None
        if exp_path is None:
            return
        try:
            queue_dir = Path(exp_path) / "queue_files"
            queue_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            suffix = ""
            if self._last_queue_path:
                try:
                    suffix = f"_{Path(self._last_queue_path).name}"
                except Exception:
                    suffix = ""
            filename = f"{prefix}_{ts}{suffix}"
            dst = queue_dir / filename
            with open(dst, "w", encoding="utf-8") as fh:
                json.dump(self._queue_payload(), fh, indent=2)
            self.log(f"Queue file copied to: {dst}")
        except Exception as exc:
            self.log(f"Queue file copy failed: {exc}")

    def _copy_queue_file_async(self, prefix: str) -> None:
        snapshot = copy.deepcopy(list(self._session.measurement_queue))

        def _worker() -> None:
            session_mgr = getattr(self._session, "session_manager", None)
            exp_path = getattr(session_mgr, "current_experiment_path", None) if session_mgr else None
            if exp_path is None:
                return
            try:
                queue_dir = Path(exp_path) / "queue_files"
                queue_dir.mkdir(parents=True, exist_ok=True)
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                suffix = ""
                if self._last_queue_path:
                    try:
                        suffix = f"_{Path(self._last_queue_path).name}"
                    except Exception:
                        suffix = ""
                filename = f"{prefix}_{ts}{suffix}"
                dst = queue_dir / filename
                payload = self._queue_payload(snapshot)
                with open(dst, "w", encoding="utf-8") as fh:
                    json.dump(payload, fh, indent=2)
                self.log(f"Queue file copied to: {dst}")
            except Exception as exc:
                self.log(f"Queue file copy failed: {exc}")

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def _extract_mux_channel(item: dict) -> Optional[int]:
        method_ref = item.get("method_ref") or {}
        mux = method_ref.get("mux_channel")
        if mux is not None:
            try:
                return int(mux)
            except (TypeError, ValueError):
                pass
        details = str(item.get("details") or "")
        m = re.search(r"\bMUX\s*ch\s*(\d+)\b", details, flags=re.IGNORECASE)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                return None
        return None

    # ── Pause / alert helpers ─────────────────────────────────────────────────

    def _exec_pause(self, seconds: float) -> bool:
        total = max(0.0, seconds)
        start = time.time()
        while self._session.is_running:
            elapsed   = time.time() - start
            remaining = total - elapsed
            if remaining <= 0:
                break
            rem = max(0.0, remaining)
            self._root.after(0, self.set_status, f"Pausing: {rem:.1f} sec remaining")
            time.sleep(min(0.5, rem))
        if not self._session.is_running:
            return False
        self._root.after(0, self.set_status, "Pause complete")
        return True

    def _exec_alert(self, message: str, *, title: str = "Paused", status_text: str | None = None) -> bool:
        if not self._session.is_running:
            return False
        done = threading.Event()
        display_status = status_text or "Paused - waiting for Continue"
        self._root.after(0, self.set_status, display_status)
        self._root.after(0, lambda: (messagebox.showinfo(title, message), done.set()))
        while self._session.is_running and not done.is_set():
            done.wait(timeout=0.2)
        if self._session.is_running:
            self._root.after(0, self.set_status, "Continue acknowledged")
        return done.is_set()

    @classmethod
    def _normalize_tip_well(cls, value) -> str:
        text = str(value or "").strip().upper()
        return text if cls._TIP_WELL_RE.fullmatch(text) else ""

    @classmethod
    def _tip_override_from_params(cls, params: dict | None) -> dict | None:
        raw = dict((params or {}).get("tip_override") or {})
        if not cls._boolish(raw.get("enabled"), default=False):
            return None
        left_tip = cls._normalize_tip_well(raw.get("left_starting_tip"))
        right_tip = cls._normalize_tip_well(raw.get("right_starting_tip"))
        if not left_tip and not right_tip:
            raise ValueError("Tip override is enabled, but no valid left/right starting tip was provided.")
        return {
            "enabled": True,
            "left_starting_tip": left_tip,
            "right_starting_tip": right_tip,
            "require_confirmation": cls._boolish(raw.get("require_confirmation"), default=True),
            "confirmation_message": str(raw.get("confirmation_message") or "").strip(),
        }

    @staticmethod
    def _format_tip_override_summary(override: dict) -> str:
        parts = []
        if override.get("left_starting_tip"):
            parts.append(f"left={override['left_starting_tip']}")
        if override.get("right_starting_tip"):
            parts.append(f"right={override['right_starting_tip']}")
        return ", ".join(parts) if parts else "no mount overrides"

    @classmethod
    def _tip_override_confirmation_message(cls, protocol_name: str, override: dict) -> str:
        summary = cls._format_tip_override_summary(override)
        return (
            f"Tip override is active for {protocol_name}.\n"
            f"Confirm the OT-2 tipracks are set to {summary} before continuing."
        )

    @classmethod
    def _apply_opentrons_tip_override(cls, source_text: str, override: dict) -> tuple[str, list[str]]:
        lines = str(source_text or "").splitlines()
        if not lines:
            raise ValueError("Protocol source is empty.")

        load_pattern = re.compile(
            r"^(?P<indent>\s*)(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*protocol\.load_instrument\([^,]+,\s*['\"](?P<mount>left|right)['\"].*tip_racks=\[(?P<tiprack>[A-Za-z_][A-Za-z0-9_]*)\]"
        )
        starting_tip_pattern = re.compile(
            r"^\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\.starting_tip\s*="
        )

        instruments_by_mount: dict[str, dict[str, str | int]] = {}
        for index, line in enumerate(lines):
            match = load_pattern.match(line)
            if not match:
                continue
            instruments_by_mount[match.group("mount")] = {
                "index": index,
                "indent": match.group("indent"),
                "var": match.group("var"),
                "tiprack": match.group("tiprack"),
            }

        requested = {
            "left": str(override.get("left_starting_tip") or "").strip().upper(),
            "right": str(override.get("right_starting_tip") or "").strip().upper(),
        }
        missing_mounts = [mount for mount, tip in requested.items() if tip and mount not in instruments_by_mount]
        if missing_mounts:
            raise ValueError(
                "Protocol does not load instrument(s) on mount(s): " + ", ".join(missing_mounts)
            )

        target_vars = {
            str(info["var"])
            for mount, info in instruments_by_mount.items()
            if requested.get(mount)
        }

        filtered_lines: list[str] = []
        for line in lines:
            match = starting_tip_pattern.match(line)
            if match and match.group("var") in target_vars:
                continue
            filtered_lines.append(line)
        lines = filtered_lines

        injected_lines: list[str] = []
        applied: list[str] = []
        for line in lines:
            injected_lines.append(line)
            match = load_pattern.match(line)
            if not match:
                continue
            mount = match.group("mount")
            tip = requested.get(mount)
            if not tip:
                continue
            indent = match.group("indent")
            instrument_var = match.group("var")
            tiprack_var = match.group("tiprack")
            injected_lines.append(f"{indent}{instrument_var}.starting_tip = {tiprack_var}[{tip!r}]")
            applied.append(f"{mount}={tip}")

        if not applied:
            raise ValueError("No matching pipette load statements were found for the requested tip override.")
        return "\n".join(injected_lines) + "\n", applied

    # ── Pump execution ────────────────────────────────────────────────────────

    def _exec_pump(self, item: dict) -> bool:
        if self._pump_ctrl is None:
            self.log("Pump backend unavailable — skipping pump action.")
            return False
        action_info = item.get("pump_action") or {}
        name        = action_info.get("name")
        params      = action_info.get("params") or {}
        details     = item.get("details", f"Pump {name}")
        collection_info = self._tracked_collection_info(params) if str(name).upper() == "HEXW2" else None
        if not name:
            self.log("Invalid pump item: missing action name."); return False
        if str(name).upper() == "STATE_RESET":
            self._session.reset_collection_tracking(reason="queue state reset step")
            self.log("Syringe state reset to 0 mL.")
            self._root.after(0, self.refresh_labels)
            return True

        if not self._pump_ctrl.connected:
            self.log("Pump not connected."); return False

        self.log(f"Queue pump -> {details}")
        try:
            if name not in {"STATUS", "STATUS_PORT"}:
                try:
                    prep = self._pump_ctrl.status_port()
                    self._log_pump_status("status port (prep)", prep)
                except Exception:
                    pass

            # Chemyx actions
            if name == "COMMAND":
                cmd = str(params.get("cmd", "")).strip()
                if not cmd:
                    raise ValueError("Missing pump command")
                resp = self._pump_ctrl.send(cmd)
                if resp:
                    self.log(f"Pump <- {resp}")
                return True

            if name == "APPLY":
                self._pump_ctrl.set_units(str(params["units"]))
                self._pump_ctrl.set_diameter_mm(float(params["diameter_mm"]))
                self._pump_ctrl.set_rate(float(params["rate"]))
                self._pump_ctrl.set_volume(float(params["volume"]))
                self._pump_ctrl.set_mode(str(params["mode"]))
                self.log("Pump APPLY executed (parameters only, no movement).")
                return True

            if name == "HEXW2":
                session_mgr = getattr(self._session, "session_manager", None)
                if collection_info is not None:
                    projected_ul = self._session.collection_volume_ul + collection_info["step_volume_ul"]
                    if collection_info["capacity_ul"] > 0 and projected_ul > collection_info["capacity_ul"]:
                        msg = (
                            "Collection syringe capacity would be exceeded. "
                            f"Projected total {format_ml_from_ul(projected_ul)} > "
                            f"{format_ml_from_ul(collection_info['capacity_ul'])}.\n\n"
                            "Empty/reset the collection syringe before continuing. "
                            "The queue will stop after you acknowledge this alert."
                        )
                        self.log(msg)
                        if session_mgr is not None:
                            session_mgr.notify_slack(f"Collection capacity hit: {msg}")
                        acknowledged = self._exec_alert(
                            msg,
                            title="Collection Capacity",
                            status_text="Collection capacity reached - waiting for Continue",
                        )
                        self._session.is_running = False
                        if acknowledged:
                            self.log("Collection capacity acknowledged; queue stopped for syringe service/reset.")
                        else:
                            self.log("Collection capacity alert interrupted; queue stopped.")
                        return False
                    if (
                        collection_info["warn_ul"] > 0
                        and projected_ul >= collection_info["warn_ul"]
                        and not self._session.collection_warned
                    ):
                        self._session.collection_warned = True
                        warn_msg = (
                            "Collection syringe is nearing capacity. "
                            f"Projected total after this pull: {format_ml_from_ul(projected_ul)}."
                        )
                        self.log(f"WARNING: {warn_msg}")
                        if session_mgr is not None:
                            session_mgr.notify_slack(f"Collection warning: {warn_msg}")

                run_kwargs = {
                    "units": str(params["units"]),
                    "mode": str(params["mode"]),
                    "diameter_mm": float(params["diameter_mm"]),
                    "volume": float(params["volume"]),
                    "rate": float(params["rate"]),
                    "delay_min": float(params.get("delay_min", 0.0)),
                    "start": bool(params.get("start", False)),
                }
                resp = self._pump_ctrl.hexw2(
                    **run_kwargs
                )
                if resp:
                    self.log(f"Pump <- {resp}")
                if bool(params.get("start", False)):
                    if not self._ensure_pump_started(run_kwargs):
                        self.log("Pump run did not start (status stayed complete/idle).")
                        return False
                    ok = self._wait_for_pump_complete(params)
                    if ok and collection_info is not None:
                        self._session.add_collection_volume(
                            volume_ul=collection_info["step_volume_ul"],
                            capacity_ul=collection_info["capacity_ul"],
                            warn_ul=collection_info["warn_ul"],
                        )
                        self.log(
                            "Collection total -> "
                            f"{self._session.collection_steps} step(s), "
                            f"{format_ml_from_ul(self._session.collection_volume_ul)} / "
                            f"{format_ml_from_ul(self._session.collection_capacity_ul)}"
                        )
                        self._root.after(0, self.refresh_labels)
                    return ok
                return True

            if name == "STATUS":
                resp = self._pump_ctrl.status()
                self._log_pump_status("status", resp)
                return True
            if name == "STATUS_PORT":
                resp = self._pump_ctrl.status_port()
                self._log_pump_status("status port", resp)
                return True
            if name == "START":
                self._pump_ctrl.start()
                return True
            if name == "PAUSE":
                self._pump_ctrl.pause(); return True
            if name == "STOP":
                self._pump_ctrl.stop(); return True
            if name == "RESTART":
                self._pump_ctrl.restart(); return True

            self.log(f"Unsupported pump action: {name}"); return False
        except Exception as exc:
            self.log(f"Pump action failed: {exc}"); return False

    def _exec_opentrons(self, item: dict, queue_index: int | None = None) -> str:
        action_info = item.get("opentrons_action") or {}
        action_name = str(action_info.get("name") or "").strip().upper()
        if action_name == "RESUME":
            return self._exec_opentrons_resume(item)
        if action_name == "HOME":
            return self._exec_opentrons_home(item)
        return self._exec_opentrons_protocol(item, queue_index=queue_index)

    def _exec_opentrons_protocol(self, item: dict, *, queue_index: int | None = None) -> str:
        action_info = item.get("opentrons_action") or {}
        params = action_info.get("params") or {}
        protocol_path = params.get("protocol_path")
        protocol_source = params.get("protocol_source")
        protocol_name = str(params.get("protocol_name") or "").strip() or "inline protocol"
        mode = str(params.get("mode") or "validate").lower()
        resume_key = str(params.get("resume_key") or "").strip()
        robot_host = str(params.get("robot_host") or "").strip() or None
        robot_port_raw = params.get("robot_port")
        try:
            robot_port = int(robot_port_raw) if robot_port_raw is not None else 31950
        except Exception:
            self.log(f"Invalid Opentrons robot_port in queue item: {robot_port_raw}")
            return "failed"
        if mode == "robot" and not robot_host:
            robot_host = str(OPENTRONS_DEFAULT_HOST or "").strip() or None
            robot_port = int(robot_port or OPENTRONS_DEFAULT_API_PORT)

        if not protocol_path and not protocol_source:
            self.log("Invalid Opentrons item: missing protocol_path/protocol_source.")
            return "failed"

        resolved = None
        if protocol_path:
            resolved = self._resolve_opentrons_protocol_path(protocol_path)
            if resolved is None and not protocol_source:
                self.log(f"Opentrons protocol not found: {protocol_path}")
                return "failed"

        effective_protocol_source = str(protocol_source) if protocol_source else None
        if effective_protocol_source is None and resolved is not None:
            try:
                effective_protocol_source = resolved.read_text(encoding="utf-8")
            except Exception as exc:
                self.log(f"[Opentrons] Could not read protocol source for overrides: {exc}")
                return "failed"

        try:
            tip_override = self._tip_override_from_params(params)
        except Exception as exc:
            self.log(f"[Opentrons] Invalid tip override settings for {protocol_name}: {exc}")
            return "failed"
        if tip_override is not None:
            if effective_protocol_source is None:
                self.log(f"[Opentrons] Tip override requested for {protocol_name}, but no protocol source is available.")
                return "failed"
            try:
                effective_protocol_source, applied = self._apply_opentrons_tip_override(
                    effective_protocol_source,
                    tip_override,
                )
            except Exception as exc:
                self.log(f"[Opentrons] Tip override failed for {protocol_name}: {exc}")
                return "failed"
            summary = ", ".join(applied)
            self.log(f"[Opentrons] Applying starting tip override for {protocol_name}: {summary}")
            if tip_override.get("require_confirmation"):
                confirm_msg = (
                    str(tip_override.get("confirmation_message") or "").strip()
                    or self._tip_override_confirmation_message(protocol_name, tip_override)
                )
                if not self._exec_alert(confirm_msg):
                    self.log(f"[Opentrons] Tip override confirmation interrupted for {protocol_name}.")
                    return "stopped"

        session_mgr = getattr(self._session, "session_manager", None)
        data_folder = getattr(session_mgr, "current_experiment_path", None) if session_mgr else None

        runner = OpentronsProtocolRunner(log_callback=self.log)
        self._active_opentrons_target = {
            "robot_host": robot_host,
            "robot_port": robot_port,
            "run_id": None,
        }
        self._session.current_stop_callback = runner.stop
        try:
            result = runner.execute_detailed(
                resolved,
                source_text=effective_protocol_source,
                protocol_name=protocol_name,
                mode=mode,
                data_folder=data_folder,
                robot_host=robot_host,
                robot_port=robot_port,
            )
        finally:
            self._session.current_stop_callback = None

        if result.state == "paused":
            if not resume_key:
                self.log(f"[Opentrons] {protocol_name} paused, but no resume key was provided.")
                return "failed"
            self._active_opentrons_target = {
                "robot_host": robot_host,
                "robot_port": robot_port,
                "run_id": result.run_id,
            }
            self._opentrons_paused_runs[resume_key] = {
                "run_id": result.run_id,
                "protocol_name": protocol_name,
                "robot_host": robot_host,
                "robot_port": robot_port,
                "queue_index": queue_index,
            }
            self.log(f"[Opentrons] Stored paused run under resume key {resume_key}.")
            self.log(f"[Opentrons] Queue deferred paused protocol: {protocol_name}")
            return "paused"

        self._opentrons_paused_runs.pop(resume_key, None)
        self._active_opentrons_target = None
        return "completed" if result.ok else ("stopped" if result.state == "stopped" else "failed")

    def _exec_opentrons_resume(self, item: dict) -> str:
        action_info = item.get("opentrons_action") or {}
        params = action_info.get("params") or {}
        resume_key = str(params.get("resume_key") or "").strip()
        protocol_name = str(params.get("protocol_name") or "").strip() or "Opentrons protocol"
        if not resume_key:
            self.log("Invalid Opentrons resume item: missing resume_key.")
            return "failed"
        self.log(f"[Opentrons] Resume requested with key {resume_key} for {protocol_name}.")

        paused = self._opentrons_paused_runs.get(resume_key)
        resolved_resume_key = resume_key
        if not paused:
            matches = [
                (key, entry)
                for key, entry in self._opentrons_paused_runs.items()
                if str(entry.get("protocol_name") or "").strip() == protocol_name
            ]
            if len(matches) == 1:
                resolved_resume_key, paused = matches[0]
                self.log(
                    "[Opentrons] Resume key mismatch; "
                    f"falling back to the only paused run for {protocol_name}."
                )
        if not paused:
            self.log(f"[Opentrons] No paused run available for resume: {protocol_name}")
            return "failed"

        run_id = str(paused.get("run_id") or "").strip()
        robot_host = str(paused.get("robot_host") or "").strip()
        robot_port = int(paused.get("robot_port") or 31950)
        if not run_id or not robot_host:
            self.log(f"[Opentrons] Paused run metadata incomplete for: {protocol_name}")
            return "failed"

        runner = OpentronsProtocolRunner(log_callback=self.log)
        self._active_opentrons_target = {
            "robot_host": robot_host,
            "robot_port": robot_port,
            "run_id": run_id,
        }
        self._session.current_stop_callback = runner.stop
        try:
            result = runner.resume_run(
                protocol_name=protocol_name,
                robot_host=robot_host,
                robot_port=robot_port,
                run_id=run_id,
            )
        finally:
            self._session.current_stop_callback = None

        if result.state == "paused":
            self._opentrons_paused_runs[resolved_resume_key] = {
                "run_id": result.run_id,
                "protocol_name": protocol_name,
                "robot_host": robot_host,
                "robot_port": robot_port,
                "queue_index": paused.get("queue_index"),
            }
            origin_index = paused.get("queue_index")
            if isinstance(origin_index, int) and 0 <= origin_index < len(self._session.measurement_queue):
                self._session.measurement_queue[origin_index]["status"] = "paused"
            self._active_opentrons_target = {
                "robot_host": robot_host,
                "robot_port": robot_port,
                "run_id": result.run_id,
            }
            self.log(f"[Opentrons] {protocol_name} paused again; queue returning to next item.")
            return "completed"

        origin_index = paused.get("queue_index")
        if isinstance(origin_index, int) and 0 <= origin_index < len(self._session.measurement_queue):
            self._session.measurement_queue[origin_index]["status"] = (
                "completed" if result.ok else ("stopped" if result.state == "stopped" else "failed")
            )
        self._opentrons_paused_runs.pop(resolved_resume_key, None)
        self._active_opentrons_target = None
        return "completed" if result.ok else ("stopped" if result.state == "stopped" else "failed")

    def _exec_opentrons_home(self, item: dict) -> str:
        action_info = item.get("opentrons_action") or {}
        params = action_info.get("params") or {}
        robot_host = str(params.get("robot_host") or "").strip()
        if not robot_host:
            self.log("Invalid Opentrons home item: missing robot_host.")
            return "failed"
        try:
            robot_port = int(params.get("robot_port") or 31950)
        except Exception:
            self.log(f"Invalid Opentrons home robot_port: {params.get('robot_port')}")
            return "failed"

        runner = OpentronsProtocolRunner(log_callback=self.log)
        return "completed" if runner.home_robot(robot_host=robot_host, robot_port=robot_port) else "failed"

    def _exec_misc(self, item: dict) -> bool:
        action_info = item.get("misc_action") or {}
        action_name = str(action_info.get("name") or "").strip().upper()
        params = dict(action_info.get("params") or {})
        session_mgr = getattr(self._session, "session_manager", None)
        if action_name != "COMPRESS_SEND":
            self.log(f"Unsupported misc action: {action_name}")
            return False

        mode = str(params.get("folder_mode") or "current_experiment").strip().lower()
        if mode == "current_experiment":
            session_mgr = getattr(self._session, "session_manager", None)
            experiment_path = getattr(session_mgr, "current_experiment_path", None) if session_mgr else None
            session_path = getattr(session_mgr, "current_session_path", None) if session_mgr else None
            source = experiment_path or session_path
            if source is None:
                self.log("Compress+Send failed: no current experiment or session folder is active.")
                if session_mgr is not None:
                    session_mgr.notify_slack("Compress+Send failed: no current experiment or session folder is active.")
                return False
            source_path = Path(source)
            if experiment_path is not None:
                self.log(f"Compress+Send source -> active experiment folder: {source_path}")
            else:
                self.log(f"Compress+Send source -> no active experiment; using current session folder: {source_path}")
        else:
            raw_folder = str(params.get("folder_path") or "").strip()
            if not raw_folder:
                self.log("Compress+Send failed: folder path is missing.")
                if session_mgr is not None:
                    session_mgr.notify_slack("Compress+Send failed: folder path is missing.")
                return False
            source_path = Path(raw_folder).expanduser()
            if not source_path.is_absolute():
                source_path = (Path.cwd() / source_path).resolve()

        if not source_path.exists() or not source_path.is_dir():
            self.log(f"Compress+Send failed: folder not found: {source_path}")
            if session_mgr is not None:
                session_mgr.notify_slack(f"Compress+Send failed: folder not found: {source_path}")
            return False

        dest_dir = Path(str(params.get("dest_dir") or r"Z:\opentrons(setup_4)"))
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self.log(f"Compress+Send failed: could not create destination {dest_dir}: {exc}")
            if session_mgr is not None:
                session_mgr.notify_slack(f"Compress+Send failed: could not create destination {dest_dir}: {exc}")
            return False

        archive_path = dest_dir / f"{source_path.name}.zip"
        try:
            self.log(f"Compress+Send -> zipping {source_path} to {archive_path}")
            shutil.make_archive(
                str(archive_path.with_suffix("")),
                "zip",
                root_dir=str(source_path),
                base_dir=".",
            )
            self.log(f"Compress+Send complete: {archive_path}")
            if session_mgr is not None:
                session_mgr.notify_slack(f"Compress+Send complete: {source_path.name} -> {archive_path}")
            return True
        except Exception as exc:
            self.log(f"Compress+Send failed: {exc}")
            if session_mgr is not None:
                session_mgr.notify_slack(f"Compress+Send failed for {source_path}: {exc}")
            return False

    def _log_pump_status(self, label: str, resp: str) -> None:
        text = (resp or "").strip()
        if not text:
            self.log(f"Pump {label}: (no response)")
            return
        self.log(f"Pump {label}: {text}")
        code = self._parse_status_code(text)
        if code is not None:
            state = self._status_text(code)
            self.log(f"Pump ops/status code: {code} ({state})")

    @staticmethod
    def _boolish(value, default: bool = False) -> bool:
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        text = str(value).strip().lower()
        if text in {"1", "true", "yes", "on"}:
            return True
        if text in {"0", "false", "no", "off", ""}:
            return False
        return bool(value)

    @classmethod
    def _should_track_collection(cls, params: dict | None) -> bool:
        payload = params or {}
        if "track_collection" in payload:
            return cls._boolish(payload.get("track_collection"), default=False)
        mode = str(payload.get("mode") or "").strip().lower()
        volume_ul = volume_to_ul(payload.get("volume"), str(payload.get("units") or ""))
        return mode == "withdraw" and (volume_ul or 0.0) > 0.0

    @staticmethod
    def _tracked_collection_info(params: dict) -> dict | None:
        if not QueueTab._should_track_collection(params):
            return None
        volume_ul = volume_to_ul((params or {}).get("volume"), str((params or {}).get("units") or ""))
        if volume_ul is None:
            return None
        try:
            capacity_ml = float((params or {}).get("collection_capacity_ml", COLLECTION_SYRINGE_CAPACITY_ML))
        except (TypeError, ValueError):
            capacity_ml = float(COLLECTION_SYRINGE_CAPACITY_ML)
        try:
            warn_ml = float((params or {}).get("collection_warn_ml", default_collection_warn_ml(capacity_ml)))
        except (TypeError, ValueError):
            warn_ml = float(default_collection_warn_ml(capacity_ml))
        return {
            "step_volume_ul": max(0.0, float(volume_ul)),
            "capacity_ul": max(0.0, capacity_ml * 1000.0),
            "warn_ul": max(0.0, warn_ml * 1000.0),
        }

    @staticmethod
    def _parse_status_code(text: str) -> int | None:
        try:
            nums = re.findall(r"(-?\d+)", text or "")
            return int(nums[-1]) if nums else None
        except Exception:
            return None

    @staticmethod
    def _status_text(code: int) -> str:
        return {0: "complete", 1: "running", 2: "paused"}.get(code, "unknown")

    def _ensure_pump_started(self, run_kwargs: dict) -> bool:
        """Confirm that a run command actually transitions out of code 0.

        Some devices can ignore the first run command depending on prior state.
        We verify status, then retry once with status-port + HEXW2 if needed.
        """
        def _poll_started(timeout_s: float = 4.0) -> bool:
            t0 = time.monotonic()
            while self._session.is_running and (time.monotonic() - t0) < timeout_s:
                try:
                    code = self._parse_status_code(self._pump_ctrl.status())
                    if code in (1, 2):
                        self.log(f"Pump start confirmed: {self._status_text(code)} (code {code})")
                        return True
                except Exception:
                    pass
                time.sleep(0.5)
            return False

        if _poll_started():
            return True

        self.log("Pump start not confirmed; retrying run command once.")
        try:
            prep = self._pump_ctrl.status_port()
            self._log_pump_status("status port (retry)", prep)
        except Exception:
            pass
        try:
            self._pump_ctrl.hexw2(**run_kwargs)
        except Exception as exc:
            self.log(f"Pump retry command failed: {exc}")
            return False
        return _poll_started()

    def _wait_for_pump_complete(self, params: dict) -> bool:
        """Block the queue until pump reports completion (status code 0).

        Confirmed mapping (user): 0=complete, 1=running, 2=paused.
        """
        if not self._session.is_running:
            return False
        # Estimate duration from volume/rate/units (best-effort).
        try:
            units = str(params.get("units") or "").lower().strip()
            volume = float(params.get("volume"))
            rate = float(params.get("rate"))
            if rate <= 0:
                est_s = 0.0
            elif units.endswith("min"):
                est_s = (volume / rate) * 60.0
            elif units.endswith("hr"):
                est_s = (volume / rate) * 3600.0
            else:
                est_s = (volume / rate) * 60.0
        except Exception:
            est_s = 0.0

        if est_s > 0:
            self.log(f"Pump wait: est. {est_s:.1f}s; verifying completion via status polling.")
        else:
            self.log("Pump wait: verifying completion via status polling.")

        start = time.monotonic()
        last_code = None
        seen_running = False
        paused_streak = 0
        unpause_attempted = False
        post_estimate_confirm_s = 1.0 if est_s > 0 else 0.0
        hard_timeout_s = (est_s + 20.0) if est_s > 0 else 15.0
        while self._session.is_running:
            try:
                resp = self._pump_ctrl.status()
                code = None
                try:
                    nums = re.findall(r"(-?\d+)", resp or "")
                    if nums:
                        code = int(nums[-1])
                except Exception:
                    code = None

                if code is not None and code != last_code:
                    last_code = code
                    state = self._status_text(code)
                    self.log(f"Pump status -> {state} (code {code})")
                if code == 1:
                    seen_running = True
                    paused_streak = 0
                elif code == 2:
                    paused_streak += 1
                else:
                    paused_streak = 0

                if code == 0:
                    elapsed = time.monotonic() - start
                    # Require either a real running state, or a post-estimate confirm delay.
                    if seen_running or elapsed >= (est_s + post_estimate_confirm_s):
                        return True

                # If pump stays paused during an active run step, nudge once.
                if code == 2 and paused_streak >= 3 and not unpause_attempted:
                    unpause_attempted = True
                    self.log("Pump paused during run step; trying status-port + start once.")
                    try:
                        prep = self._pump_ctrl.status_port()
                        self._log_pump_status("status port (auto-recover)", prep)
                    except Exception:
                        pass
                    try:
                        self._pump_ctrl.start()
                    except Exception:
                        pass
            except Exception as exc:
                self.log(f"Pump status poll failed: {exc}")

            elapsed = time.monotonic() - start
            if elapsed >= hard_timeout_s:
                self.log("Pump wait timeout: completion not confirmed.")
                return False

            # Poll faster near/after estimated end.
            if est_s > 0 and elapsed >= max(0.0, est_s - 2.0):
                time.sleep(0.5)
            else:
                time.sleep(1.0)

        return False
