"""
gui/tab_pump.py - Chemyx syringe pump control tab.

Provides direct pump control (connect, status, start/pause/stop, parameter
setting) and optional queue integration via `on_add_to_queue`.
"""

from __future__ import annotations

import threading
from tkinter import messagebox
import tkinter as tk
from tkinter import ttk

from config import (
    CHEMYX_DEFAULT_PORT,
    CHEMYX_DEFAULT_BAUD,
    CHEMYX_DEFAULT_EOL,
    CHEMYX_DEFAULT_UNITS,
    CHEMYX_DEFAULT_DIAMETER_MM,
    CHEMYX_DEFAULT_RATE,
    CHEMYX_DEFAULT_VOLUME,
)

try:
    from pump import list_serial_ports
except Exception:
    list_serial_ports = None  # type: ignore[assignment]


class PumpTab:
    def __init__(self, parent_frame, pump_ctrl, on_add_to_queue, root: tk.Tk):
        self._frame = parent_frame
        self._ctrl = pump_ctrl
        self._add_to_queue = on_add_to_queue
        self._root = root

        self._busy = False
        self._early_logs: list[str] = []
        self._log_text: tk.Text | None = None
        self._disable_group: list[ttk.Widget] = []

        if self._ctrl is None:
            ttk.Label(
                self._frame,
                text="Pump controls unavailable (pyserial not installed).",
                foreground="gray",
            ).pack(pady=40)
            return

        self._build()

    # ---- UI -----------------------------------------------------------------

    def _build(self) -> None:
        pad = {"padx": 6, "pady": 4}

        container = ttk.Frame(self._frame)
        container.pack(fill="both", expand=True, padx=10, pady=10)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(3, weight=1)

        # Connection
        conn = ttk.LabelFrame(container, text="Connection (Chemyx)")
        conn.grid(row=0, column=0, sticky="ew")
        conn.columnconfigure(1, weight=1)

        self._var_sim = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            conn,
            text="Simulate (no hardware)",
            variable=self._var_sim,
            command=self._on_sim_toggle,
        ).grid(row=0, column=0, columnspan=2, sticky="w", **pad)

        ttk.Label(conn, text="Port:").grid(row=1, column=0, sticky="e", **pad)
        self._var_port = tk.StringVar(value=str(CHEMYX_DEFAULT_PORT))
        self._port_combo = ttk.Combobox(conn, textvariable=self._var_port, values=[], width=18)
        self._port_combo.grid(row=1, column=1, sticky="w", **pad)

        self._btn_refresh = ttk.Button(conn, text="Refresh", command=self._refresh_ports)
        self._btn_refresh.grid(row=1, column=2, sticky="w", **pad)

        ttk.Label(conn, text="Baud:").grid(row=2, column=0, sticky="e", **pad)
        self._var_baud = tk.StringVar(value=str(int(CHEMYX_DEFAULT_BAUD)))
        self._baud_combo = ttk.Combobox(
            conn,
            textvariable=self._var_baud,
            values=["38400", "9600", "115200", "57600", "19200", "14400"],
            width=18,
        )
        self._baud_combo.grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(conn, text="EOL:").grid(row=2, column=2, sticky="e", **pad)
        self._var_eol = tk.StringVar(value=str(CHEMYX_DEFAULT_EOL))
        self._eol_combo = ttk.Combobox(conn, textvariable=self._var_eol, values=["cr", "lf", "crlf"], width=7)
        self._eol_combo.grid(row=2, column=3, sticky="w", **pad)

        ttk.Button(conn, text="Connect", command=lambda: self._threaded(self._do_connect)).grid(
            row=3, column=0, sticky="w", **pad
        )
        ttk.Button(
            conn, text="Auto-connect", command=lambda: self._threaded(self._do_autoconnect)
        ).grid(row=3, column=1, sticky="w", **pad)
        ttk.Button(conn, text="Disconnect", command=lambda: self._threaded(self._do_disconnect)).grid(
            row=3, column=2, sticky="w", **pad
        )

        self._lbl_conn = ttk.Label(conn, text="Disconnected", foreground="gray")
        self._lbl_conn.grid(row=3, column=3, sticky="e", **pad)

        # Parameters
        params = ttk.LabelFrame(container, text="Parameters")
        params.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        params.columnconfigure(1, weight=1)

        ttk.Label(params, text="Units:").grid(row=0, column=0, sticky="e", **pad)
        self._var_units = tk.StringVar(value=str(CHEMYX_DEFAULT_UNITS))
        ttk.Combobox(
            params,
            textvariable=self._var_units,
            values=["mLmin", "mLhr", "uLmin", "uLhr"],
            width=18,
        ).grid(row=0, column=1, sticky="w", **pad)

        ttk.Label(params, text="Diameter (mm):").grid(row=0, column=2, sticky="e", **pad)
        self._var_diam = tk.StringVar(value=str(CHEMYX_DEFAULT_DIAMETER_MM))
        ttk.Entry(params, textvariable=self._var_diam, width=10).grid(
            row=0, column=3, sticky="w", **pad
        )

        ttk.Label(params, text="Rate:").grid(row=1, column=0, sticky="e", **pad)
        self._var_rate = tk.StringVar(value=str(CHEMYX_DEFAULT_RATE))
        ttk.Entry(params, textvariable=self._var_rate, width=10).grid(
            row=1, column=1, sticky="w", **pad
        )

        ttk.Label(params, text="Volume:").grid(row=1, column=2, sticky="e", **pad)
        self._var_vol = tk.StringVar(value=str(CHEMYX_DEFAULT_VOLUME))
        ttk.Entry(params, textvariable=self._var_vol, width=10).grid(
            row=1, column=3, sticky="w", **pad
        )

        ttk.Label(params, text="Mode:").grid(row=2, column=0, sticky="e", **pad)
        self._var_mode = tk.StringVar(value="infuse")
        ttk.Combobox(params, textvariable=self._var_mode, values=["infuse", "withdraw"], width=18).grid(
            row=2, column=1, sticky="w", **pad
        )

        ttk.Button(params, text="Apply", command=lambda: self._threaded(self._do_apply)).grid(
            row=2, column=2, sticky="w", **pad
        )
        ttk.Button(params, text="Run (hexw2)", command=lambda: self._threaded(self._do_hexw2_start)).grid(
            row=2, column=3, sticky="w", **pad
        )

        ttk.Button(params, text="Queue Apply", command=self._queue_apply).grid(
            row=3, column=2, sticky="w", **pad
        )
        ttk.Button(params, text="Queue Run", command=self._queue_hexw2_start).grid(
            row=3, column=3, sticky="w", **pad
        )

        # Controls
        ctl = ttk.LabelFrame(container, text="Controls")
        ctl.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        ttk.Button(ctl, text="Status", command=lambda: self._threaded(self._do_status)).grid(
            row=0, column=0, sticky="w", **pad
        )
        ttk.Button(ctl, text="Status Port", command=lambda: self._threaded(self._do_status_port)).grid(
            row=0, column=1, sticky="w", **pad
        )
        ttk.Button(ctl, text="Start", command=lambda: self._threaded(self._do_start)).grid(
            row=0, column=2, sticky="w", **pad
        )
        ttk.Button(ctl, text="Pause", command=lambda: self._threaded(self._do_pause)).grid(
            row=0, column=3, sticky="w", **pad
        )
        ttk.Button(ctl, text="Stop", command=lambda: self._threaded(self._do_stop)).grid(
            row=0, column=4, sticky="w", **pad
        )
        ttk.Button(ctl, text="Restart", command=lambda: self._threaded(self._do_restart)).grid(
            row=0, column=5, sticky="w", **pad
        )

        ttk.Label(ctl, text="Raw cmd:").grid(row=1, column=0, sticky="e", **pad)
        self._var_raw = tk.StringVar(value="")
        raw_entry = ttk.Entry(ctl, textvariable=self._var_raw, width=50)
        raw_entry.grid(row=1, column=1, columnspan=4, sticky="ew", **pad)
        raw_entry.bind("<Return>", lambda _e: self._threaded(self._do_raw_send))
        ttk.Button(ctl, text="Send", command=lambda: self._threaded(self._do_raw_send)).grid(
            row=1, column=5, sticky="w", **pad
        )
        ttk.Button(ctl, text="Queue Raw", command=self._queue_raw_send).grid(
            row=2, column=5, sticky="w", **pad
        )

        # Log
        log_frame = ttk.LabelFrame(container, text="Log")
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(10, 0))
        log_frame.columnconfigure(0, weight=1)
        log_frame.rowconfigure(0, weight=1)
        self._log_text = tk.Text(log_frame, height=10, state="disabled")
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self._flush_early_logs()

        self._disable_group = [
            self._port_combo,
            self._btn_refresh,
            self._baud_combo,
            self._eol_combo,
            raw_entry,
        ]
        self._refresh_ports()
        self._on_sim_toggle()
        self._sync_conn_label()

    # ---- Public helpers ------------------------------------------------------

    def log(self, msg: str) -> None:
        if self._log_text is None:
            self._early_logs.append(msg)
            return

        def _append() -> None:
            assert self._log_text is not None
            self._log_text.configure(state="normal")
            self._log_text.insert("end", msg + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self._root.after(0, _append)

    def autoconnect(self) -> None:
        if self._ctrl is None:
            return
        if getattr(self._ctrl, "connected", False):
            return
        try:
            ports = list_serial_ports() if callable(list_serial_ports) else []
        except Exception:
            ports = []
        if not ports:
            try:
                self._var_sim.set(True)
                self._on_sim_toggle()
            except Exception:
                pass
        self._threaded(self._do_autoconnect)

    # ---- Internals -----------------------------------------------------------

    def _flush_early_logs(self) -> None:
        if self._log_text is None or not self._early_logs:
            return
        msgs = self._early_logs[:]
        self._early_logs.clear()

        def _flush() -> None:
            assert self._log_text is not None
            self._log_text.configure(state="normal")
            for m in msgs:
                self._log_text.insert("end", m + "\n")
            self._log_text.see("end")
            self._log_text.configure(state="disabled")

        self._root.after(0, _flush)

    def _set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)

        def _apply() -> None:
            for w in self._disable_group:
                try:
                    w.configure(state=("disabled" if self._busy else "normal"))
                except Exception:
                    pass

        self._root.after(0, _apply)

    def _threaded(self, fn) -> None:
        if self._ctrl is None:
            messagebox.showerror("Pump Error", "Pump backend unavailable.")
            return
        if self._busy:
            return

        def run() -> None:
            try:
                self._set_busy(True)
                fn()
            except Exception as exc:
                self.log(f"ERROR: {exc}")
                self._root.after(0, lambda: messagebox.showerror("Pump Error", str(exc)))
            finally:
                self._set_busy(False)
                self._sync_conn_label()

        threading.Thread(target=run, daemon=True).start()

    def _sync_conn_label(self) -> None:
        if self._ctrl is None:
            return
        connected = bool(getattr(self._ctrl, "connected", False))
        sim = bool(getattr(self._ctrl, "simulate", False))

        def _apply() -> None:
            label = "Connected" if connected else "Disconnected"
            if connected and sim:
                label = "Connected (SIM)"
            self._lbl_conn.configure(
                text=label,
                foreground=("green" if connected else "gray"),
            )

        self._root.after(0, _apply)

    def _on_sim_toggle(self) -> None:
        sim = bool(getattr(self, "_var_sim", tk.BooleanVar(value=False)).get())
        if sim:
            current = self._var_port.get().strip()
            if current and current.upper() != "SIM":
                self._last_real_port = current  # type: ignore[attr-defined]
            self._var_port.set("SIM")
        else:
            last = getattr(self, "_last_real_port", "").strip()
            if last:
                self._var_port.set(last)
        state = "disabled" if sim else "normal"
        try:
            self._port_combo.configure(state=state)
        except Exception:
            pass
        try:
            self._btn_refresh.configure(state=state)
        except Exception:
            pass

    def _refresh_ports(self) -> None:
        if list_serial_ports is None:
            ports: list[str] = []
        else:
            try:
                ports = list_serial_ports()
            except Exception:
                ports = []
        self._port_combo.configure(values=ports)
        if ports and not self._var_port.get().strip():
            self._var_port.set(ports[0])

    def _require_connected(self) -> None:
        if self._ctrl is None or not self._ctrl.connected:
            raise RuntimeError("Pump not connected")

    # ---- Actions -------------------------------------------------------------

    def _do_connect(self) -> None:
        port = self._var_port.get().strip()
        baud = int(self._var_baud.get().strip())
        eol = self._var_eol.get().strip()
        simulate = bool(self._var_sim.get())
        self._ctrl.connect(port=(port or "SIM"), baudrate=baud, timeout_s=1.0, eol=eol, simulate=simulate)
        self.log(f"[Pump] Connected {port} @ {baud}")

    def _do_autoconnect(self) -> None:
        port = self._var_port.get().strip()
        eol = self._var_eol.get().strip()
        simulate = bool(self._var_sim.get())
        baud = self._ctrl.auto_connect(port=(port or "SIM"), timeout_s=1.0, eol=eol, simulate=simulate)
        self._var_baud.set(str(int(baud)))
        self.log(f"[Pump] Auto-connected {port} @ {baud}")

    def _do_disconnect(self) -> None:
        if self._ctrl.connected:
            self._ctrl.disconnect()

    def _do_apply(self) -> None:
        self._require_connected()
        self._ctrl.set_units(self._var_units.get())
        self._ctrl.set_diameter_mm(float(self._var_diam.get()))
        self._ctrl.set_rate(float(self._var_rate.get()))
        self._ctrl.set_volume(float(self._var_vol.get()))
        self._ctrl.set_mode(self._var_mode.get())
        self.log("[Pump] Parameters applied")

    def _do_hexw2_start(self) -> None:
        self._require_connected()
        resp = self._ctrl.hexw2(
            units=self._var_units.get(),
            mode=self._var_mode.get(),
            diameter_mm=float(self._var_diam.get()),
            volume=float(self._var_vol.get()),
            rate=float(self._var_rate.get()),
            delay_min=0.0,
            start=True,
        )
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_status(self) -> None:
        self._require_connected()
        resp = self._ctrl.status()
        self.log(f"[Pump] status: {resp or '(no response)'}")

    def _do_status_port(self) -> None:
        self._require_connected()
        resp = self._ctrl.status_port()
        self.log(f"[Pump] status port: {resp or '(no response)'}")

    def _do_start(self) -> None:
        self._require_connected()
        resp = self._ctrl.start()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_pause(self) -> None:
        self._require_connected()
        resp = self._ctrl.pause()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_stop(self) -> None:
        self._require_connected()
        resp = self._ctrl.stop()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_restart(self) -> None:
        self._require_connected()
        resp = self._ctrl.restart()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_raw_send(self) -> None:
        self._require_connected()
        cmd = self._var_raw.get().strip()
        if not cmd:
            return
        resp = self._ctrl.send(cmd)
        self.log(f">> {cmd}")
        if resp:
            self.log(f"<< {resp}")

    # ---- Queue helpers -------------------------------------------------------

    def _queue_add(self, action_name: str, params: dict, details: str) -> None:
        if not callable(self._add_to_queue):
            return
        item = {
            "type": "pump",
            "details": details,
            "pump_action": {"name": action_name, "params": params},
        }
        try:
            self._add_to_queue(item)
            self.log(f"[Queue] Added: {details}")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))

    def _queue_apply(self) -> None:
        self._queue_add(
            "APPLY",
            {
                "units": self._var_units.get(),
                "diameter_mm": float(self._var_diam.get()),
                "rate": float(self._var_rate.get()),
                "volume": float(self._var_vol.get()),
                "mode": self._var_mode.get(),
            },
            "Pump: apply parameters",
        )

    def _queue_hexw2_start(self) -> None:
        self._queue_add(
            "HEXW2",
            {
                "units": self._var_units.get(),
                "mode": self._var_mode.get(),
                "diameter_mm": float(self._var_diam.get()),
                "volume": float(self._var_vol.get()),
                "rate": float(self._var_rate.get()),
                "delay_min": 0.0,
                "start": True,
            },
            "Pump: run (hexw2 start)",
        )

    def _queue_raw_send(self) -> None:
        cmd = self._var_raw.get().strip()
        if not cmd:
            return
        self._queue_add("COMMAND", {"cmd": cmd}, f"Pump cmd: {cmd}")
