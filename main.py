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
import tkinter as tk

# Make sure the package root is on sys.path so all imports resolve
ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from gui.app import ElectrochemGUI


def main():
    root = tk.Tk()
    _app = ElectrochemGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
