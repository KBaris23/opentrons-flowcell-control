# Electrochem Flowcell Console + Chemyx Syringe Pump

## Run

1. Install dependencies:
   - `pip install -r requirements.txt`
2. Start the GUI:
   - `python main.py`

Windows venv workflow:
- `powershell -ExecutionPolicy Bypass -File scripts\\setup_venv.ps1`
- `powershell -ExecutionPolicy Bypass -File scripts\\run_gui.ps1`

## Pump control (Chemyx)

- The pump tab uses `pyserial` to talk to Chemyx Fusion pumps (Basic Mode).
- You can send raw commands; any command starting with `clrf` is automatically preceded by `status port` (helps when the pump is paused).
- Use "Simulate (no hardware)" in the pump tab to test pump actions without a connected device.

## Opentrons integration

- The app now includes an `Opentrons` tab for file-based OT-2 protocol inspection, queueing, optional simulation, and UI-built protocol generation.
- Builder-generated protocols can be run immediately, queued without saving, or saved into the Opentrons protocol library.
- PalmSens execution still stays on the existing MethodSCRIPT path; the experimental `pypalmsens` sample files are intentionally not part of runtime control.
- Bundled protocol files live under `opentrons_protocols/`.
- Opentrons simulation is optional and only works when the `opentrons` Python package is installed locally.

## Roadmap

- Add true robot execution beyond file validation/simulation once the target OT-2 deployment path is finalized.
- Add coordinated parallel orchestration for PalmSens + Opentrons after device lifecycles, stop semantics, and experiment synchronization are promoted into a shared scheduler layer.

## Slack bot (optional)

This repo includes the full Slack Events bot again (disabled by default).

Environment variables:
- `EA_SLACK_ENABLE=1`
- `EA_SLACK_BOT_TOKEN=...` (xoxb-...)
- `EA_SLACK_SIGNING_SECRET=...`
- `EA_SLACK_TARGET=...` (channel/DM id for outbound notifications)
- `EA_SLACK_PORT=8765` (optional)

Inbound endpoint:
- `http://<host>:<EA_SLACK_PORT>/slack/events`

For local machines you can optionally use ngrok:
- `EA_NGROK_AUTOSTART=1`
- `EA_NGROK_PATH=...` (path to `ngrok.exe`)
- `EA_NGROK_DOMAIN=...` (optional, if you have a reserved domain)

## Project layout

- `main.py` - GUI entrypoint
- `gui/` - Tkinter UI tabs
- `core/` - session + measurement execution logic
- `pump/` - Chemyx pump driver (`pump/chemyx.py`)
- `robot/` - optional robot/protocol helpers (`robot/opentrons_runner.py`)
- `methods/` - saved MethodSCRIPT library
- `opentrons_protocols/` - curated OT-2 protocol files for the GUI
- `recipe_maker/` - recipe blocks and saved recipes
