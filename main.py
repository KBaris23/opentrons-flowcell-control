"""
main.py — Application entry point.

Run with:
    python main.py

Requires:
  - Python 3.10+
  - Dependencies in requirements.txt (pyserial for Chemyx pump control)
"""
#!/usr/bin/env python3
# electrochemistry_automation_gui.py
# To run -> python main.py

import sys
import os
import signal
import tkinter as tk

# Make sure the package root is on sys.path so all imports resolve
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui.app import ElectrochemGUI


def main():
    root = tk.Tk()
    app = ElectrochemGUI(root)

    def _request_close():
        try:
            app.request_close()
        except Exception:
            try:
                root.quit()
            except Exception:
                pass
            try:
                root.destroy()
            except Exception:
                pass

    def _handle_sigint(_signum, _frame):
        _request_close()

    try:
        signal.signal(signal.SIGINT, _handle_sigint)
    except Exception:
        pass

    def _signal_keepalive():
        if getattr(app, "_closing", False):
            return
        try:
            root.after(100, _signal_keepalive)
        except Exception:
            pass

    root.after(100, _signal_keepalive)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        _request_close()


if __name__ == "__main__":
    main()
