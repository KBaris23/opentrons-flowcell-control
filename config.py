"""
config.py — Application-wide constants and defaults.

Edit this file to change hardware defaults, paths, and version info.
All other modules import from here — never hardcode constants elsewhere.
"""

from pathlib import Path
import os

# ── Version ──────────────────────────────────────────────────────────────────
APP_VERSION = "1.0.0"

# ── Pump hardware defaults ────────────────────────────────────────────────────

# Chemyx Fusion pump defaults (serial, Basic Mode)
# Leave blank to auto-select from detected serial ports at runtime.
CHEMYX_DEFAULT_PORT = ""
CHEMYX_DEFAULT_BAUD = 9600
CHEMYX_DEFAULT_EOL  = "cr"   # "cr", "lf", or "crlf"
CHEMYX_DEFAULT_UNITS        = "uLmin"
CHEMYX_DEFAULT_DIAMETER_MM  = 11.73
CHEMYX_DEFAULT_RATE         = 1.0
CHEMYX_DEFAULT_VOLUME       = 25.0
COLLECTION_SYRINGE_CAPACITY_ML = 5.0
COLLECTION_SYRINGE_WARN_FRACTION = 0.9
FLOWCELL_FILL_VOLUME_UL = 225.0
FLOWCELL_FILL_TARGET_S = 5.0

# Common syringe inner diameters (ID) in mm.
# These are typical values and can vary by manufacturer/model — verify your syringe datasheet.
SYRINGE_PRESETS_MM: dict[str, float] = {
    "1 mL (typical)": 4.7,
    "3 mL (typical)": 8.7,
    "5 mL (typical)": 12.1,
    "10 mL (typical)": 14.5,
    "20 mL (typical)": 19.1,
    "30 mL (typical)": 21.6,
    "50/60 mL (typical)": 26.6,
}

# ── File / folder paths ───────────────────────────────────────────────────────
METHODS_DIR     = Path("methods")           # where .ms scripts are saved
DATA_DIR        = Path(os.getenv("EA_DATA_DIR", "measurement_data"))  # where measurement CSVs land
BLOCKS_DIR      = Path("recipe_maker") / "default_blocks"  # where block definitions are saved
OPENTRONS_PROTOCOLS_DIR = Path("opentrons_protocols")      # bundled / curated OT-2 protocol files
OPENTRONS_LIBRARY_DIR = OPENTRONS_PROTOCOLS_DIR / "library"
OPENTRONS_LIBRARY_MAP_FILE = OPENTRONS_PROTOCOLS_DIR / "library_map.json"
SAVE_DATED_METHOD_COPIES = False            # if True, also write methods/YYYY-MM-DD/*.ms working copies
#keep in mind that methods are already double saved under library and the experiments where they are used

# ── Serial device detection keywords ─────────────────────────────────────────
DEVICE_KEYWORDS = ["ESPicoDev", "EmStat", "USB Serial Port", "FTDI"]
DEVICE_BAUDRATE = 230_400

# Opentrons defaults
OPENTRONS_DEFAULT_RUN_MODE = "validate"
OPENTRONS_DEFAULT_HOST = "169.254.229.52"
OPENTRONS_DEFAULT_API_PORT = 31950

# ── GUI geometry ──────────────────────────────────────────────────────────────
WINDOW_GEOMETRY = "1400x900"
WINDOW_TITLE    = f"Opentrons Flowcell Console  v{APP_VERSION}"

# Slack integration (optional)
# Set these via environment variables on the machine running the GUI.
SLACK_ENABLE         = os.getenv("EA_SLACK_ENABLE", "0").strip().lower() in ("1", "true", "yes", "on")
SLACK_BOT_TOKEN      = os.getenv("EA_SLACK_BOT_TOKEN", "").strip()
SLACK_SIGNING_SECRET = os.getenv("EA_SLACK_SIGNING_SECRET", "").strip()
SLACK_TARGET         = os.getenv("EA_SLACK_TARGET", "").strip()  # channel ID (C/G) or DM ID (D)
SLACK_PORT           = int(os.getenv("EA_SLACK_PORT", "8765"))
SLACK_ONLY_WHEN_EXPERIMENT = os.getenv("EA_SLACK_ONLY_WHEN_EXPERIMENT", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# ngrok integration (optional, for Slack Events API on local machines)
NGROK_AUTOSTART = os.getenv("EA_NGROK_AUTOSTART", "0").strip().lower() in (
    "1", "true", "yes", "on"
)
NGROK_PATH = os.getenv("EA_NGROK_PATH", "").strip()
NGROK_DOMAIN = os.getenv("EA_NGROK_DOMAIN", "").strip()

