from __future__ import annotations

import time
import threading
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Optional


BAUD_CANDIDATES: list[int] = [38400, 9600, 115200, 57600, 19200, 14400]

UNITS_MAP = {
    "mlmin": 0,  # mL/min
    "mlhr": 1,  # mL/hr
    "ulmin": 2,  # uL/min
    "ulhr": 3,  # uL/hr
    "ml/min": 0,
    "ml/hr": 1,
    "ul/min": 2,
    "ul/hr": 3,
}

EOL_MAP = {
    "cr": "\r",
    "lf": "\n",
    "crlf": "\r\n",
}


def list_serial_ports() -> list[str]:
    try:
        from serial.tools import list_ports  # type: ignore[import-not-found]

        return [p.device for p in list_ports.comports()]
    except Exception:
        return []


@dataclass(frozen=True)
class ChemyxConnectionSettings:
    port: str
    baudrate: int = 38400
    timeout_s: float = 1.0
    eol: str = "cr"
    simulate: bool = False


class _SimChemyxBackend:
    def __init__(self) -> None:
        self.units = 2  # uL/min
        self.mode = 0  # 0=infuse, 1=withdraw
        self.diameter_mm = 11.73
        self.volume = 0.0
        self.rate = 0.0
        self.delay_min = 0.0
        self.running = False
        self.paused = False

    def close(self) -> None:
        self.running = False
        self.paused = False

    def send(self, cmd: str) -> str:
        c = (cmd or "").strip()
        low = c.lower()
        if not c:
            return ""

        if low == "help":
            return "SIM: help, pump status, status port, start/pause/stop/restart, set ..., hexw2 ..."

        if low == "pump status":
            state = "paused" if self.paused else ("running" if self.running else "stopped")
            return f"SIM: {state}"

        if low == "status port":
            return "SIM: port ok"

        if low == "start":
            self.running = True
            self.paused = False
            return "SIM: started"

        if low == "pause":
            if self.running:
                self.paused = True
            return "SIM: paused"

        if low == "stop":
            self.running = False
            self.paused = False
            return "SIM: stopped"

        if low == "restart":
            self.running = False
            self.paused = False
            return "SIM: restarted"

        if low.startswith("set "):
            parts = low.split()
            if len(parts) >= 3 and parts[1] == "units":
                try:
                    self.units = int(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            if len(parts) >= 3 and parts[1] == "mode":
                try:
                    self.mode = int(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            if len(parts) >= 3 and parts[1] == "diameter":
                try:
                    self.diameter_mm = float(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            if len(parts) >= 3 and parts[1] == "volume":
                try:
                    self.volume = float(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            if len(parts) >= 3 and parts[1] == "rate":
                try:
                    self.rate = float(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            if len(parts) >= 3 and parts[1] == "delay":
                try:
                    self.delay_min = float(parts[2])
                except Exception:
                    pass
                return "SIM: ok"
            return "SIM: ok"

        if low.startswith("hexw2 "):
            # hexw2 <units> <mode> <diameter> <volume> <rate> <delay> [start]
            parts = c.split()
            try:
                self.units = int(parts[1])
                self.mode = int(parts[2])
                self.diameter_mm = float(parts[3])
                self.volume = float(parts[4])
                self.rate = float(parts[5])
                self.delay_min = float(parts[6])
            except Exception:
                pass
            if parts and parts[-1].lower() == "start":
                self.running = True
                self.paused = False
                return "SIM: hexw2 started"
            return "SIM: hexw2 set"

        if low.startswith("clrf"):
            return "SIM: ok"

        return "SIM: ok"


class ChemyxPumpCtrl:
    """Chemyx Fusion pump controller (serial, Basic Mode).

    This is intentionally small and GUI-friendly:
    - Thread-safe send() with a single internal lock
    - Minimal convenience methods for common commands

    Quirk handling:
    - Some setups only accept `clrf ...` commands reliably, and require a
      preceding `status port` query when the pump is paused. We enforce this
      ordering automatically for any command starting with `clrf`.
    """

    def __init__(self, log_cb: Callable[[str], None] = print, verbose: bool = False) -> None:
        self._log_cb = log_cb
        self._verbose = verbose
        self._lock = threading.Lock()
        self._ser: Optional[Any] = None
        self._sim: Optional[_SimChemyxBackend] = None
        self._eol = "\r"
        self._settings: Optional[ChemyxConnectionSettings] = None

    @property
    def connected(self) -> bool:
        if self._sim is not None:
            return True
        return bool(self._ser and bool(getattr(self._ser, "is_open", False)))

    @property
    def simulate(self) -> bool:
        return self._sim is not None

    @property
    def settings(self) -> Optional[ChemyxConnectionSettings]:
        return self._settings

    def connect(
        self,
        *,
        port: str,
        baudrate: int = 38400,
        timeout_s: float = 1.0,
        eol: str = "cr",
        simulate: bool = False,
    ) -> None:
        eol_key = eol.lower().strip()
        if eol_key not in EOL_MAP:
            raise ValueError(f"Unknown EOL: {eol}. Use one of {list(EOL_MAP.keys())}")
        with self._lock:
            if self.connected:
                self.disconnect()
            self._eol = EOL_MAP[eol_key]
            if simulate:
                self._sim = _SimChemyxBackend()
                self._ser = None
                self._settings = ChemyxConnectionSettings(
                    port=(port or "SIM"),
                    baudrate=int(baudrate),
                    timeout_s=float(timeout_s),
                    eol=eol_key,
                    simulate=True,
                )
                self._log("[Pump] Connected (simulation)")
                return

            try:
                import serial  # type: ignore[import-not-found]
            except Exception as exc:
                raise ImportError(
                    "pyserial is required for Chemyx pump control. Install with: pip install pyserial"
                ) from exc

            self._sim = None
            self._ser = serial.Serial(
                port=port,
                baudrate=int(baudrate),
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=float(timeout_s),
                write_timeout=float(timeout_s),
            )
            self._settings = ChemyxConnectionSettings(
                port=port,
                baudrate=int(baudrate),
                timeout_s=float(timeout_s),
                eol=eol_key,
                simulate=False,
            )
        self._log(f"[Pump] Connected: {port} @ {baudrate} baud")

    def auto_connect(
        self,
        *,
        port: str,
        baud_candidates: Iterable[int] = BAUD_CANDIDATES,
        timeout_s: float = 1.0,
        eol: str = "cr",
        simulate: bool = False,
    ) -> int:
        if simulate:
            self.connect(port=(port or "SIM"), baudrate=38400, timeout_s=timeout_s, eol=eol, simulate=True)
            return 38400

        last_err: Optional[Exception] = None
        for baud in baud_candidates:
            try:
                self.connect(port=port, baudrate=int(baud), timeout_s=timeout_s, eol=eol, simulate=False)
                resp = self.help()
                if resp:
                    self._log(f"[Pump] Auto-connected at {baud} baud")
                    return int(baud)
                self.disconnect()
            except Exception as exc:
                last_err = exc
                try:
                    self.disconnect()
                except Exception:
                    pass
        raise RuntimeError(f"Auto-connect failed for {port}") from last_err

    def disconnect(self) -> None:
        with self._lock:
            if self._sim is not None:
                try:
                    self._sim.close()
                finally:
                    self._sim = None
            if self._ser and bool(getattr(self._ser, "is_open", False)):
                try:
                    self._ser.close()
                finally:
                    self._ser = None
            self._settings = None
        self._log("[Pump] Disconnected")

    def _log(self, msg: str) -> None:
        try:
            self._log_cb(msg)
        except Exception:
            # Logging should never break hardware control.
            pass

    def _read_all(self) -> str:
        if self._sim is not None:
            return ""
        assert self._ser is not None
        time.sleep(0.1)
        data = b""
        while self._ser.in_waiting:
            data += self._ser.read(self._ser.in_waiting)
            time.sleep(0.05)
        return data.decode(errors="ignore").strip()

    def _send_raw(self, cmd: str) -> str:
        if self._sim is not None:
            resp = self._sim.send(cmd)
            if self._verbose:
                self._log(f">> {cmd}")
                self._log(f"<< {resp if resp else '(no response)'}")
            return resp

        if not self.connected or self._ser is None:
            raise RuntimeError("Pump not connected")
        line = (cmd.strip() + self._eol).encode("ascii", errors="ignore")
        if self._verbose:
            self._log(f">> {cmd}")
        self._ser.reset_input_buffer()
        self._ser.write(line)
        self._ser.flush()
        resp = self._read_all()
        if self._verbose:
            self._log(f"<< {resp if resp else '(no response)'}")
        return resp

    def send(self, cmd: str) -> str:
        cmd_stripped = cmd.strip()
        if not cmd_stripped:
            return ""
        cmd_low = cmd_stripped.lower()
        with self._lock:
            if cmd_low.startswith("clrf"):
                # Field quirk: ensure a status query precedes clrf when paused.
                try:
                    self._send_raw("status port")
                except Exception:
                    # Best-effort; still attempt clrf.
                    pass
            return self._send_raw(cmd_stripped)

    # ---- Convenience commands ------------------------------------------------

    def help(self) -> str:
        return self.send("help")

    def status(self) -> str:
        return self.send("pump status")

    def status_port(self) -> str:
        return self.send("status port")

    def start(self) -> str:
        return self.send("start")

    def pause(self) -> str:
        return self.send("pause")

    def stop(self) -> str:
        return self.send("stop")

    def restart(self) -> str:
        return self.send("restart")

    def set_units(self, units: str) -> str:
        units_key = units.lower().strip()
        if units_key not in UNITS_MAP:
            units_norm = units_key.replace(" ", "").replace("_", "").replace("\\", "/")
            if units_norm in UNITS_MAP:
                units_key = units_norm
            else:
                raise ValueError(f"Unknown units: {units}. Use one of {list(UNITS_MAP.keys())}")
        return self.send(f"set units {UNITS_MAP[units_key]}")

    def set_diameter_mm(self, mm: float) -> str:
        return self.send(f"set diameter {float(mm)}")

    def set_volume(self, value: float) -> str:
        return self.send(f"set volume {float(value)}")

    def set_rate(self, value: float) -> str:
        return self.send(f"set rate {float(value)}")

    def set_delay_min(self, minutes: float) -> str:
        return self.send(f"set delay {float(minutes)}")

    def set_time_min(self, minutes: float) -> str:
        return self.send(f"set time {float(minutes)}")

    def set_mode(self, mode: str) -> str:
        mode_key = mode.lower().strip()
        if mode_key in {"withdraw", "1"}:
            return self.send("set mode 1")
        if mode_key in {"infuse", "0"}:
            return self.send("set mode 0")
        raise ValueError("Mode must be infuse/withdraw or 0/1")

    def hexw2(
        self,
        *,
        units: str,
        mode: str,
        diameter_mm: float,
        volume: float,
        rate: float,
        delay_min: float = 0.0,
        start: bool = False,
    ) -> str:
        units_key = units.lower().strip()
        if units_key not in UNITS_MAP:
            units_key = units_key.replace(" ", "").replace("_", "").replace("\\", "/")
        if units_key not in UNITS_MAP:
            raise ValueError(f"Unknown units: {units}. Use one of {list(UNITS_MAP.keys())}")
        mode_val = "1" if mode.lower().strip() in {"withdraw", "1"} else "0"
        cmd = (
            f"hexw2 {UNITS_MAP[units_key]} {mode_val} {float(diameter_mm)} "
            f"{float(volume)} {float(rate)} {float(delay_min)}"
        )
        if start:
            cmd += " start"
        return self.send(cmd)
