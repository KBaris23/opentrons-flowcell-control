"""
gui/tab_pump.py - Chemyx syringe pump control tab.

Provides direct pump control (connect, status, start/pause/stop, parameter
setting) and optional queue integration via `on_add_to_queue`.
"""

from __future__ import annotations

import re
import threading
import time
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
    SYRINGE_PRESETS_MM,
)

try:
    from pump import default_serial_port, ranked_serial_ports
except Exception:
    ranked_serial_ports = None  # type: ignore[assignment]
    default_serial_port = None  # type: ignore[assignment]


class PumpTab:
    def __init__(self, parent_frame, pump_ctrl, on_add_to_queue, root: tk.Tk, session=None):
        self._frame = parent_frame
        self._ctrl = pump_ctrl
        self._add_to_queue = on_add_to_queue
        self._root = root
        self._session = session

        self._busy = False
        self._early_logs: list[str] = []
        self._log_text: tk.Text | None = None
        self._disable_group: list[ttk.Widget] = []

        # Run monitor (best-effort estimate based on commanded rate/volume).
        # Not all pumps/firmware report live dispensed volume, so we estimate from
        # the last GUI command.
        self._run_active = False
        self._run_paused = False
        self._run_started = 0.0
        self._run_paused_at = 0.0
        self._run_pause_accum_s = 0.0
        self._run_target: float | None = None
        self._run_rate_per_s: float | None = None
        self._run_units = ""
        self._lbl_run: ttk.Label | None = None
        self._var_auto_prep: tk.BooleanVar | None = None
        self._var_poll_status: tk.BooleanVar | None = None
        self._last_status_code: int | None = None
        self._last_status_poll = 0.0
        self._status_poll_inflight = False

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
        pad = {"padx": 4, "pady": 2}

        container = ttk.Frame(self._frame)
        container.pack(fill="both", expand=True, padx=8, pady=8)
        container.columnconfigure(0, weight=1)
        container.rowconfigure(0, weight=1)

        panes = ttk.Panedwindow(container, orient="horizontal")
        panes.grid(row=0, column=0, sticky="nsew")

        top = ttk.Frame(panes)
        top.columnconfigure(0, weight=1)
        top.columnconfigure(1, weight=1)
        panes.add(top, weight=1)

        # Connection
        conn = ttk.LabelFrame(top, text="Connection (Chemyx)")
        conn.grid(row=0, column=0, sticky="ew", padx=(0, 8))
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
        params = ttk.LabelFrame(top, text="Parameters")
        params.grid(row=1, column=0, sticky="ew", padx=(0, 8), pady=(6, 0))
        params.columnconfigure(1, weight=1)
        params.columnconfigure(3, weight=1)

        ttk.Label(
            params,
            text="Diameter is syringe inner diameter (ID).",
            foreground="#666",
        ).grid(row=0, column=0, columnspan=4, padx=6, pady=(0, 2), sticky="w")

        self._lbl_run = ttk.Label(params, text="Run: idle", foreground="#555")
        self._lbl_run.grid(row=0, column=2, columnspan=2, padx=6, pady=(0, 2), sticky="e")

        self._var_auto_prep = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            params,
            text="Auto status-port before Apply/Run",
            variable=self._var_auto_prep,
        ).grid(row=5, column=0, columnspan=2, padx=6, pady=(2, 0), sticky="w")

        self._var_poll_status = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            params,
            text="Poll status during run",
            variable=self._var_poll_status,
        ).grid(row=5, column=2, columnspan=2, padx=6, pady=(2, 0), sticky="w")

        ttk.Label(params, text="Units:").grid(row=1, column=0, sticky="e", **pad)
        self._var_units = tk.StringVar(value=str(CHEMYX_DEFAULT_UNITS))
        ttk.Combobox(
            params,
            textvariable=self._var_units,
            values=["mLmin", "mLhr", "uLmin", "uLhr"],
            width=18,
        ).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(params, text="Syringe preset:").grid(row=1, column=2, sticky="e", **pad)
        self._var_syringe = tk.StringVar(value="Custom")
        syringe_values = ["Custom"] + sorted(SYRINGE_PRESETS_MM.keys())
        self._syringe_combo = ttk.Combobox(
            params,
            textvariable=self._var_syringe,
            values=syringe_values,
            state="readonly",
            width=18,
        )
        self._syringe_combo.grid(row=1, column=3, sticky="w", **pad)
        self._syringe_combo.bind("<<ComboboxSelected>>", lambda _e: self._apply_syringe_preset())

        ttk.Label(params, text="Diameter (mm):").grid(row=2, column=0, sticky="e", **pad)
        self._var_diam = tk.StringVar(value=str(CHEMYX_DEFAULT_DIAMETER_MM))
        ttk.Entry(params, textvariable=self._var_diam, width=10).grid(row=2, column=1, sticky="w", **pad)

        ttk.Label(params, text="Mode:").grid(row=2, column=2, sticky="e", **pad)
        self._var_mode = tk.StringVar(value="infuse")
        ttk.Combobox(params, textvariable=self._var_mode, values=["infuse", "withdraw"], width=18).grid(
            row=2, column=3, sticky="w", **pad
        )

        ttk.Label(params, text="Rate:").grid(row=3, column=0, sticky="e", **pad)
        self._var_rate = tk.StringVar(value=str(CHEMYX_DEFAULT_RATE))
        ttk.Entry(params, textvariable=self._var_rate, width=10).grid(
            row=3, column=1, sticky="w", **pad
        )

        ttk.Label(params, text="Volume:").grid(row=3, column=2, sticky="e", **pad)
        self._var_vol = tk.StringVar(value=str(CHEMYX_DEFAULT_VOLUME))
        ttk.Entry(params, textvariable=self._var_vol, width=10).grid(
            row=3, column=3, sticky="w", **pad
        )

        ttk.Button(params, text="Apply", command=lambda: self._threaded(self._do_apply)).grid(
            row=4, column=0, sticky="w", **pad
        )
        ttk.Button(params, text="Run (hexw2)", command=lambda: self._threaded(self._do_hexw2_start)).grid(
            row=4, column=1, sticky="w", **pad
        )

        ttk.Button(params, text="Queue Apply", command=self._queue_apply).grid(
            row=4, column=2, sticky="w", **pad
        )
        ttk.Button(params, text="Queue Run", command=self._queue_hexw2_start).grid(
            row=4, column=3, sticky="w", **pad
        )

        # Controls
        ctl = ttk.LabelFrame(top, text="Controls")
        ctl.grid(row=0, column=1, rowspan=2, sticky="nsew")
        for c in range(6):
            ctl.columnconfigure(c, weight=1)

        ttk.Button(ctl, text="▶ Start", command=lambda: self._threaded(self._do_start)).grid(
            row=0, column=0, sticky="w", **pad
        )
        ttk.Button(ctl, text="⏸ Pause", command=lambda: self._threaded(self._do_pause)).grid(
            row=0, column=1, sticky="w", **pad
        )
        ttk.Button(ctl, text="■ Stop", command=lambda: self._threaded(self._do_stop)).grid(
            row=0, column=2, sticky="w", **pad
        )

        ttk.Button(ctl, text="Restart", command=lambda: self._threaded(self._do_restart)).grid(
            row=1, column=0, sticky="w", **pad
        )
        ttk.Button(ctl, text="Pump Status", command=lambda: self._threaded(self._do_status)).grid(
            row=1, column=1, sticky="w", **pad
        )
        ttk.Button(ctl, text="Port Status", command=lambda: self._threaded(self._do_status_port)).grid(
            row=1, column=2, sticky="w", **pad
        )
        ttk.Button(ctl, text="Reset Syringe State", command=self._reset_syringe_state).grid(
            row=1, column=3, sticky="w", **pad
        )
        ttk.Button(ctl, text="Queue Reset Step", command=self._queue_state_reset).grid(
            row=1, column=4, sticky="w", **pad
        )

        rawf = ttk.LabelFrame(ctl, text="Raw Command")
        rawf.grid(row=2, column=0, columnspan=6, sticky="ew", padx=6, pady=(6, 4))
        rawf.columnconfigure(1, weight=1)

        ttk.Label(rawf, text="Command:").grid(row=0, column=0, sticky="e", **pad)
        self._var_raw = tk.StringVar(value="")
        raw_entry = ttk.Entry(rawf, textvariable=self._var_raw)
        raw_entry.grid(row=0, column=1, sticky="ew", **pad)
        raw_entry.bind("<Return>", lambda _e: self._threaded(self._do_raw_send))
        ttk.Button(rawf, text="Send to Pump", command=lambda: self._threaded(self._do_raw_send)).grid(
            row=0, column=2, sticky="w", **pad
        )
        ttk.Button(rawf, text="Add to Queue", command=self._queue_raw_send).grid(
            row=0, column=3, sticky="w", **pad
        )
        ttk.Label(
            rawf,
            text=(
                "Send to Pump runs immediately. Add to Queue appends a step that runs when the Queue runs."
            ),
            foreground="#666",
        ).grid(row=1, column=0, columnspan=4, padx=6, pady=(0, 6), sticky="w")

        # Log
        log_frame = ttk.LabelFrame(panes, text="Log")
        log_frame.columnconfigure(0, weight=1)
        log_frame.columnconfigure(1, weight=0)
        log_frame.rowconfigure(0, weight=1)
        panes.add(log_frame, weight=2)

        self._log_text = tk.Text(log_frame, height=12, state="disabled")
        self._log_text.grid(row=0, column=0, sticky="nsew", padx=(6, 0), pady=6)
        yscroll = ttk.Scrollbar(log_frame, orient="vertical", command=self._log_text.yview)
        yscroll.grid(row=0, column=1, sticky="ns", padx=(4, 6), pady=6)
        self._log_text.configure(yscrollcommand=yscroll.set)
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
        self._start_run_ticker()

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
            ports = ranked_serial_ports() if callable(ranked_serial_ports) else []
        except Exception:
            ports = []
        if not ports:
            try:
                self._var_sim.set(True)
                self._on_sim_toggle()
            except Exception:
                pass
        elif not self._var_port.get().strip():
            self._var_port.set(ports[0])
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
                msg = str(exc)
                self._root.after(0, lambda m=msg: messagebox.showerror("Pump Error", m))
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

    def _apply_syringe_preset(self) -> None:
        key = (getattr(self, "_var_syringe", tk.StringVar(value="Custom")).get() or "").strip()
        if not key or key == "Custom":
            return
        mm = SYRINGE_PRESETS_MM.get(key)
        if mm is None:
            return
        try:
            self._var_diam.set(str(float(mm)))
            self.log(f"[Pump] Syringe preset -> {key} ({float(mm):g} mm ID)")
        except Exception:
            pass

    # ---- Run monitor --------------------------------------------------------

    def _start_run_ticker(self) -> None:
        def _tick() -> None:
            try:
                self._update_run_monitor()
                self._maybe_poll_pump_status()
            finally:
                self._root.after(250, _tick)

        self._root.after(250, _tick)

    @staticmethod
    def _units_to_rate_per_s(units: str, rate: float) -> float:
        u = (units or "").strip().lower()
        if u.endswith("min"):
            return float(rate) / 60.0
        if u.endswith("hr"):
            return float(rate) / 3600.0
        return float(rate) / 60.0

    def _start_monitor(self, *, target_volume: float | None, rate: float | None, units: str) -> None:
        self._run_active = True
        self._run_paused = False
        self._run_started = time.monotonic()
        self._run_paused_at = 0.0
        self._run_pause_accum_s = 0.0
        self._run_target = float(target_volume) if target_volume is not None else None
        self._run_rate_per_s = (
            self._units_to_rate_per_s(units, float(rate)) if rate is not None else None
        )
        self._run_units = str(units or "").strip()
        self._update_run_monitor(force=True)

    def _pause_monitor(self) -> None:
        if not self._run_active or self._run_paused:
            return
        self._run_paused = True
        self._run_paused_at = time.monotonic()
        self._update_run_monitor(force=True)

    def _stop_monitor(self) -> None:
        self._run_active = False
        self._run_paused = False
        self._update_run_monitor(force=True)

    def _update_run_monitor(self, force: bool = False) -> None:
        if self._lbl_run is None:
            return

        if not self._run_active:
            self._lbl_run.configure(text="Run: idle")
            return

        state = "paused" if self._run_paused else "running"
        pump_state = None
        if self._last_status_code is not None:
            pump_state = {0: "complete", 1: "running", 2: "paused"}.get(self._last_status_code, str(self._last_status_code))
        units = self._run_units or ""
        if self._run_rate_per_s is None:
            self._lbl_run.configure(text=f"Run: {state}")
            return

        now = time.monotonic()
        elapsed = max(0.0, now - self._run_started - self._run_pause_accum_s)
        if self._run_paused and self._run_paused_at:
            elapsed = max(0.0, self._run_paused_at - self._run_started - self._run_pause_accum_s)
        disp = self._run_rate_per_s * elapsed

        if self._run_target is not None:
            shown = min(disp, self._run_target)
            suffix = f" — pump={pump_state}" if pump_state else ""
            if disp >= self._run_target and not self._run_paused:
                self._lbl_run.configure(
                    text=f"Run: est. done — {shown:.1f}/{self._run_target:g} {units}{suffix}"
                )
            else:
                self._lbl_run.configure(text=f"Run: {state} — est. {shown:.1f}/{self._run_target:g} {units}{suffix}")
        else:
            suffix = f" — pump={pump_state}" if pump_state else ""
            self._lbl_run.configure(text=f"Run: {state} — est. {disp:.1f} {units}{suffix}")

    @staticmethod
    def _parse_last_int(txt: str) -> int | None:
        nums = re.findall(r"(-?\d+)", txt or "")
        if not nums:
            return None
        try:
            return int(nums[-1])
        except Exception:
            return None

    def _maybe_poll_pump_status(self) -> None:
        if not self._run_active or self._run_paused:
            return
        if self._ctrl is None or not getattr(self._ctrl, "connected", False):
            return
        if self._var_poll_status is not None and not bool(self._var_poll_status.get()):
            return
        now = time.monotonic()
        if self._status_poll_inflight:
            return

        interval = 1.0
        try:
            if self._run_target is not None and self._run_rate_per_s is not None:
                elapsed = max(0.0, now - self._run_started - self._run_pause_accum_s)
                est_disp = self._run_rate_per_s * elapsed
                remaining = (self._run_target - est_disp) / max(self._run_rate_per_s, 1e-9)
                if remaining <= 2.0:
                    interval = 0.5
        except Exception:
            interval = 1.0

        if now - self._last_status_poll < interval:
            return
        self._last_status_poll = now
        self._status_poll_inflight = True

        def _poll() -> None:
            try:
                resp = self._ctrl.status()
                code = self._parse_last_int(resp or "")
                if code is not None:
                    self._last_status_code = code
                    if code == 0:
                        # Treat 0 as "stopped/complete" (common pattern; best-effort).
                        self._root.after(0, lambda: self.log("[Pump] status indicates stop/complete (code 0)"))
                        self._root.after(0, self._stop_monitor)
            except Exception:
                pass
            finally:
                self._status_poll_inflight = False

        threading.Thread(target=_poll, daemon=True).start()

    def _refresh_ports(self) -> None:
        if ranked_serial_ports is None:
            ports: list[str] = []
        else:
            try:
                ports = ranked_serial_ports()
            except Exception:
                ports = []
        prev = self._var_port.get().strip()
        self._port_combo.configure(values=ports)
        if not ports:
            return
        if prev and prev in ports:
            return
        if callable(default_serial_port):
            picked = (default_serial_port() or "").strip()
            if picked in ports:
                self._var_port.set(picked)
                return
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
        self._stop_monitor()

    def _do_apply(self) -> None:
        self._require_connected()
        if self._var_auto_prep is None or bool(self._var_auto_prep.get()):
            try:
                self._log_status_response("status port", self._ctrl.status_port())
            except Exception:
                pass
        self._ctrl.set_units(self._var_units.get())
        self._ctrl.set_diameter_mm(float(self._var_diam.get()))
        self._ctrl.set_rate(float(self._var_rate.get()))
        self._ctrl.set_volume(float(self._var_vol.get()))
        self._ctrl.set_mode(self._var_mode.get())
        self.log("[Pump] Parameters applied")

    def _do_hexw2_start(self) -> None:
        self._require_connected()
        units = self._var_units.get()
        rate = float(self._var_rate.get())
        volume = float(self._var_vol.get())
        if self._var_auto_prep is None or bool(self._var_auto_prep.get()):
            try:
                self._log_status_response("status port", self._ctrl.status_port())
            except Exception:
                pass
        resp = self._ctrl.hexw2(
            units=units,
            mode=self._var_mode.get(),
            diameter_mm=float(self._var_diam.get()),
            volume=volume,
            rate=rate,
            delay_min=0.0,
            start=True,
        )
        self._start_monitor(target_volume=volume, rate=rate, units=units)
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_status(self) -> None:
        self._require_connected()
        resp = self._ctrl.status()
        self._log_status_response("pump status", resp)

    def _do_status_port(self) -> None:
        self._require_connected()
        resp = self._ctrl.status_port()
        self._log_status_response("status port", resp)

    def _do_start(self) -> None:
        self._require_connected()
        if self._var_auto_prep is None or bool(self._var_auto_prep.get()):
            try:
                self._log_status_response("status port", self._ctrl.status_port())
            except Exception:
                pass
        resp = self._ctrl.start()
        self._start_monitor(target_volume=None, rate=None, units=self._var_units.get())
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_pause(self) -> None:
        self._require_connected()
        resp = self._ctrl.pause()
        self._pause_monitor()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_stop(self) -> None:
        self._require_connected()
        resp = self._ctrl.stop()
        self._stop_monitor()
        if resp:
            self.log(f"[Pump] {resp}")

    def _do_restart(self) -> None:
        self._require_connected()
        resp = self._ctrl.restart()
        self._stop_monitor()
        if resp:
            self.log(f"[Pump] {resp}")

    def _log_status_response(self, label: str, resp: str) -> None:
        txt = (resp or "").strip()
        if not txt:
            self.log(f"[Pump] {label}: (no response)")
            return
        self.log(f"[Pump] {label}: {txt}")
        code = self._parse_last_int(txt)
        if code is not None:
            state = {0: "complete", 1: "running", 2: "paused"}.get(code, "unknown")
            self.log(f"[Pump] ops/status code: {code} ({state}) from {label}")

    def _do_raw_send(self) -> None:
        self._require_connected()
        cmd = self._var_raw.get().strip()
        if not cmd:
            return
        resp = self._ctrl.send(cmd)
        self.log(f">> {cmd}")
        if resp:
            self.log(f"<< {resp}")

    def _reset_syringe_state(self) -> None:
        if self._session is None:
            messagebox.showwarning("Unavailable", "Session state is not available.")
            return
        if not messagebox.askyesno(
            "Reset Syringe State",
            "Reset the persistent syringe state to 0 mL?\nUse this after the collection syringe has been emptied.",
        ):
            return
        self._session.reset_collection_tracking(reason="manual fluidics reset")
        self.log("[Pump] Syringe state reset to 0 mL.")

    # ---- Queue helpers -------------------------------------------------------

    def _queue_add(self, action_name: str, params: dict, details: str) -> None:
        if not callable(self._add_to_queue):
            return
        exec_name = action_name
        if action_name.startswith("HEXW2_"):
            exec_name = "HEXW2"
        item = {
            "type": f"PUMP_{action_name}",
            "status": "pending",
            "details": details,
            "pump_action": {"name": exec_name, "params": params},
        }
        try:
            self._add_to_queue(item)
            self.log(f"[Queue] Added: {details}")
        except Exception as exc:
            messagebox.showerror("Queue Error", str(exc))

    def _queue_apply(self) -> None:
        units = self._var_units.get()
        mode = self._var_mode.get()
        diam = float(self._var_diam.get())
        rate = float(self._var_rate.get())
        vol = float(self._var_vol.get())
        self._queue_add(
            "APPLY",
            {
                "units": units,
                "diameter_mm": diam,
                "rate": rate,
                "volume": vol,
                "mode": mode,
            },
            (
                f"Pump APPLY mode={mode} units={units} "
                f"diameter_mm={diam:g} volume={vol:g} rate={rate:g}"
            ),
        )

    def _queue_hexw2_start(self) -> None:
        units = self._var_units.get()
        mode = self._var_mode.get()
        diam = float(self._var_diam.get())
        rate = float(self._var_rate.get())
        vol = float(self._var_vol.get())
        action_type = f"HEXW2_{mode.upper()}"
        self._queue_add(
            action_type,
            {
                "units": units,
                "mode": mode,
                "diameter_mm": diam,
                "volume": vol,
                "rate": rate,
                "delay_min": 0.0,
                "start": True,
            },
            (
                f"Pump RUN mode={mode} units={units} "
                f"diameter_mm={diam:g} volume={vol:g} rate={rate:g} delay_min=0"
            ),
        )

    def _queue_raw_send(self) -> None:
        cmd = self._var_raw.get().strip()
        if not cmd:
            return
        self._queue_add("COMMAND", {"cmd": cmd}, f"Pump cmd: {cmd}")

    def _queue_state_reset(self) -> None:
        self._queue_add("STATE_RESET", {}, "Syringe state reset to 0 mL")
