"""
scripts/pump_smoke_test.py

Quick, non-GUI smoke test for the Chemyx pump backend.

Examples (PowerShell):
  .\\.venv\\Scripts\\python.exe scripts\\pump_smoke_test.py --simulate --verbose
  .\\.venv\\Scripts\\python.exe scripts\\pump_smoke_test.py --list-ports
  .\\.venv\\Scripts\\python.exe scripts\\pump_smoke_test.py --port COM6 --baud 38400 --status-port --status
  .\\.venv\\Scripts\\python.exe scripts\\pump_smoke_test.py --port COM6 --cmd \"clrf\" --cmd \"help\"
"""

from __future__ import annotations

import argparse
import sys

from pump.chemyx import BAUD_CANDIDATES, ChemyxPumpCtrl, list_serial_ports


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Chemyx pump backend smoke test")
    p.add_argument("--list-ports", action="store_true", help="List available serial ports and exit")
    p.add_argument("--simulate", action="store_true", help="Run in simulation mode (no hardware)")
    p.add_argument("--port", default="", help="Serial port (e.g. COM6)")
    p.add_argument("--baud", type=int, default=38400, help="Baud rate (default: 38400)")
    p.add_argument("--eol", default="cr", choices=["cr", "lf", "crlf"], help="Line ending")
    p.add_argument("--auto", action="store_true", help="Auto-connect (tries common baud rates)")
    p.add_argument("--verbose", action="store_true", help="Print raw TX/RX lines")
    p.add_argument("--status-port", action="store_true", help="Run `status port`")
    p.add_argument("--status", action="store_true", help="Run pump status query")
    p.add_argument("--help-cmd", action="store_true", help="Run `help` command")
    p.add_argument("--cmd", action="append", default=[], help="Send raw command (repeatable)")
    return p


def main(argv: list[str]) -> int:
    args = _build_parser().parse_args(argv)

    if args.list_ports:
        ports = list_serial_ports()
        if not ports:
            print("(no serial ports found)")
            return 0
        for p in ports:
            print(p)
        return 0

    ctrl = ChemyxPumpCtrl(log_cb=print, verbose=bool(args.verbose))

    try:
        if args.simulate:
            ctrl.connect(port=(args.port or "SIM"), baudrate=args.baud, eol=args.eol, simulate=True)
        elif args.auto:
            if not args.port:
                raise SystemExit("--port is required unless --simulate is set.")
            ctrl.auto_connect(
                port=args.port,
                baud_candidates=BAUD_CANDIDATES,
                eol=args.eol,
                simulate=False,
            )
        else:
            if not args.port:
                raise SystemExit("--port is required unless --simulate is set.")
            ctrl.connect(port=args.port, baudrate=args.baud, eol=args.eol, simulate=False)

        print(f"Connected: {ctrl.settings}")

        if args.help_cmd:
            resp = ctrl.help()
            print(f"help -> {resp or '(no response)'}")

        if args.status_port:
            resp = ctrl.status_port()
            print(f"status port -> {resp or '(no response)'}")

        if args.status:
            resp = ctrl.status()
            print(f"status -> {resp or '(no response)'}")

        for cmd in args.cmd:
            cmd_s = (cmd or "").strip()
            if not cmd_s:
                continue
            resp = ctrl.send(cmd_s)
            print(f">> {cmd_s}")
            print(f"<< {resp or '(no response)'}")

        return 0

    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            if ctrl.connected:
                ctrl.disconnect()
        except Exception:
            pass


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

