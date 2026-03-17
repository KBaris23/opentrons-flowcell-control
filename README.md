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

## Roadmap

- Opentrons control integration will be added later (structure is intended to keep `pump/` separate from future robot control modules).

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
- `methods/` - saved MethodSCRIPT library
- `recipe_maker/` - recipe blocks and saved recipes
