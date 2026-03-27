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
- Collection syringe state is now persisted in `measurement_data/syringe_state_registry.json`, so tracked volume is preserved across app restarts.
- Use the `STATE_RESET` recipe/queue step, or the `Reset Syringe State` button in the Queue tab, after you physically empty the collection syringe.
- Alert steps already exist in both the Recipe Maker and Queue editors via the `ALERT` action.

## Syringe tracking workflow

- Enable `Track collected volume` on tracked `HEXW2` withdraw steps to add each pull to the persistent syringe registry.
- Queue runs now reuse the last saved syringe total instead of clearing it automatically at start.
- When the syringe has been emptied, add the `Syringe State Reset` block or run the `waste_disposal_reset.json` template recipe to zero the stored state.

## Included recipe templates

- `recipe_maker/saved_recipes/flowcell_cleaning_di.json`:
  three `225 uL` DI withdraw pulls with alerts before and after the cleaning sequence.
- `recipe_maker/saved_recipes/waste_disposal_reset.json`:
  alert the operator to empty the collection syringe, then reset the persistent tracked state to `0 mL`.

## Opentrons integration

- The app now includes an `Opentrons` tab for file-based OT-2 protocol inspection, queueing, optional simulation, and UI-built protocol generation.
- Builder-generated protocols can be run immediately, queued without saving, or saved into the Opentrons protocol library.
- The Protocol Builder now supports a configurable pipette `starting_tip` and shows a live tip-budget estimate from that start well to the end of the rack so you can catch tip exhaustion before a run.
- The `Opentrons` tab now includes a `Home OT-2` button and an `Add Home` queue step for manual recovery.
- Stopping the queue now also tries to stop any tracked Opentrons run and send the robot home, which helps recover from protocols left paused mid-run.
- PalmSens execution still stays on the existing MethodSCRIPT path; the experimental `pypalmsens` sample files are intentionally not part of runtime control.
- Bundled protocol files live under `opentrons_protocols/`.
- Opentrons simulation is optional and only works when the `opentrons` Python package is installed locally.

## Worked example: 5-step titration recipe

This is a full UI-driven example that shows how to build an Opentrons titration protocol, pair it with an SWV method, and run the whole experiment through the recipe/queue flow.

Assumed setup:
- Concentrated stock is in `stock:A1` in a 1.5 mL rack.
- Diluted titration tube is in `dilute:A1` in a 50 mL rack.
- Each titration addition is `5 uL`.
- There are `5` titration additions total.
- Each flowcell pull is `225 uL`.
- Target pull time is `5 s`.
- Pump rate is therefore about `2700 uL/min`.
- The collection syringe for this example is `50 mL`, so the warning point is `45 mL`.
- You want an SWV after every addition, including the 5th.

### 1. Build the 5-step Opentrons protocol

Go to `Opentrons -> Protocol Builder`.

In `Setup`, enter:
- `Name`: `Titration_5x5uL`
- `Description`: `5-step titration, pause after each addition`
- `Pipette`: `p20_single_gen2`
- `Pipette side`: your real mount, for example `left`
- `Tiprack alias`: `tips`

Add these labware rows:
- alias `tips`, load name `opentrons_96_filtertiprack_20ul`, slot `4`
- alias `stock`, load name `opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap`, slot `5`
- alias `dilute`, load name `opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical`, slot `6`

Go to `Steps` and add these exact protocol steps:

1. `comment`
   Text: `Titration step 1`
2. `transfer`
   Volume: `5`
   Source Alias: `stock`
   Source Well: `A1`
   Dest Alias: `dilute`
   Dest Well: `A1`
   New Tip: `always`
3. `pause`
   Message: `Run syringe pull and SWV for titration step 1, then resume.`
4. `comment`
   Text: `Titration step 2`
5. `transfer`
   Volume: `5`
   Source Alias: `stock`
   Source Well: `A1`
   Dest Alias: `dilute`
   Dest Well: `A1`
   New Tip: `always`
6. `pause`
   Message: `Run syringe pull and SWV for titration step 2, then resume.`
7. `comment`
   Text: `Titration step 3`
8. `transfer`
   Volume: `5`
   Source Alias: `stock`
   Source Well: `A1`
   Dest Alias: `dilute`
   Dest Well: `A1`
   New Tip: `always`
9. `pause`
   Message: `Run syringe pull and SWV for titration step 3, then resume.`
10. `comment`
    Text: `Titration step 4`
11. `transfer`
    Volume: `5`
    Source Alias: `stock`
    Source Well: `A1`
    Dest Alias: `dilute`
    Dest Well: `A1`
    New Tip: `always`
12. `pause`
    Message: `Run syringe pull and SWV for titration step 4, then resume.`
13. `comment`
    Text: `Titration step 5`
14. `transfer`
    Volume: `5`
    Source Alias: `stock`
    Source Well: `A1`
    Dest Alias: `dilute`
    Dest Well: `A1`
    New Tip: `always`
15. `pause`
    Message: `Run syringe pull and SWV for titration step 5, then resume to finish.`
16. `comment`
    Text: `Titration additions complete`

Go to `Generated Preview` and confirm you see five `pipette.transfer(...)` lines and five `protocol.pause(...)` lines.

Click `Save to Library`.

### 2. Create the SWV method entry

Because the Recipe Maker pulls SWV methods from the saved method library, do this once:

1. Go to `Methods`.
2. Set your SWV parameters the way you want.
3. Click `Add to Queue`.

That saves the SWV method into the library map. After that, you can clear the queue if you want.

### 3. Build the recipe

Go to `Recipes`.

In `Pump Steps`:
- set `Pump action` to `HEXW2`
- set `Units` to `uLmin`
- set `Mode` to `withdraw`
- set syringe preset to `50/60 mL (typical)`
- click `Preset Flowcell Pull`

That should give you:
- volume `225`
- target ETA `5`
- calculated rate about `2700`
- tracking enabled
- capacity `50`
- warning at `45`

Now build the recipe in this exact order.

First add the protocol start:
1. Open the `Opentrons` subtab in `Recipes`.
2. Choose `Titration_5x5uL`.
3. Set run mode to `robot`.
4. Click `Add Protocol Step`.

Then add the first pull:
1. Go back to `Pump Steps`.
2. Confirm the flowcell pull values.
3. Click `Add Pump Step`.

Then add the SWV step:
1. Go to `Method Library`.
2. Select your SWV method.
3. Click `Add Method Step`.

Then add the resume:
1. Go back to `Opentrons`.
2. Choose the same protocol.
3. Click `Add Resume Step`.

Now repeat that same `Pump Step -> SWV Step -> Resume Step` block four more times.

Your final recipe should be exactly:

1. `OPENTRONS_PROTOCOL` for `Titration_5x5uL`
2. `PUMP_HEXW2` withdraw `225 uL`
3. `SWV`
4. `OPENTRONS_RESUME`
5. `PUMP_HEXW2`
6. `SWV`
7. `OPENTRONS_RESUME`
8. `PUMP_HEXW2`
9. `SWV`
10. `OPENTRONS_RESUME`
11. `PUMP_HEXW2`
12. `SWV`
13. `OPENTRONS_RESUME`
14. `PUMP_HEXW2`
15. `SWV`
16. `OPENTRONS_RESUME`

Why this order works:
- step 1 starts the robot and it pauses after titration step 1
- steps 2 and 3 do the pull and measurement for that concentration
- step 4 resumes the robot so it performs titration step 2 and pauses again
- the pattern repeats
- the last `OPENTRONS_RESUME` lets the protocol finish after the 5th SWV

### 4. Sanity checks before running

Check these before you start:
- In the Recipe Maker, the collection summary should read about `1.125 mL`
- Because `5 x 225 uL = 1125 uL = 1.125 mL`
- The pump step details should show about `eta 5.0s`
- The protocol selected in every Opentrons step is the same one
- The SWV method is the correct one
- The OT-2 file uses the correct rack load names and slots
- The stock and dilute wells are both really `A1`

### 5. Send to queue and run

1. In `Recipes`, click `Send to Queue`.
2. Go to `Run Queue`.
3. Confirm the queue order matches the 16-item list above.
4. Make sure the pump is connected.
5. Make sure the OT-2 host/IP is correct.
6. Start the queue.

What physically happens:
- Robot adds `5 uL` stock to the dilute tube and pauses.
- Syringe withdraws `225 uL` into the flowcell.
- SWV runs on that new concentration.
- Robot resumes, adds the next `5 uL`, and pauses again.
- Repeat until all 5 titration points are done.

## Alternative worked example: 5-step titration with multiple protocols and starting tips

The pause/resume pattern above is still valid and is often the cleanest option when you want one OT-2 protocol to own the whole titration. But now that the builder supports `starting_tip`, you can also split the titration into multiple small protocols and place each one on a known tip index.

This version uses:
- `5` separate Opentrons protocols
- `1` transfer per protocol
- no `pause` steps
- no `OPENTRONS_RESUME` steps
- explicit starting tips so the protocols do not fight over automatic tip tracking

The same assumptions from the first worked example still apply:
- stock is `stock:A1`
- dilute tube is `dilute:A1`
- each addition is `5 uL`
- each flowcell pull is `225 uL`
- you want an SWV after every addition

### 1. Build five single-transfer protocols

Go to `Opentrons -> Protocol Builder`.

Use the same deck setup each time:
- alias `tips`, load name `opentrons_96_filtertiprack_20ul`, slot `4`
- alias `stock`, load name `opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap`, slot `5`
- alias `dilute`, load name `opentrons_10_tuberack_falcon_4x50ml_6x15ml_conical`, slot `6`

Create five saved protocols with the same transfer step but different names and `Starting tip` values:

1. `Titration_5x5uL_step1`
   Starting tip: `A1`
2. `Titration_5x5uL_step2`
   Starting tip: `B1`
3. `Titration_5x5uL_step3`
   Starting tip: `C1`
4. `Titration_5x5uL_step4`
   Starting tip: `D1`
5. `Titration_5x5uL_step5`
   Starting tip: `E1`

For each of those protocols, use:
- `Pipette`: `p20_single_gen2`
- `Pipette side`: your real mount, for example `left`
- `Tiprack alias`: `tips`

Add these exact steps:

1. `comment`
   Text: `Titration step N`
2. `transfer`
   Volume: `5`
   Source Alias: `stock`
   Source Well: `A1`
   Dest Alias: `dilute`
   Dest Well: `A1`
   New Tip: `always`
3. `comment`
   Text: `Titration step N complete`

Replace `N` with `1` through `5` for each saved protocol.

In `Generated Preview`, each protocol should show one `pipette.transfer(...)` line and no `protocol.pause(...)` lines. You should also see the builder tip budget reflect the chosen start well for that protocol.

### 2. Reuse the same SWV method and pump setup

Use the same SWV library entry and the same `HEXW2` flowcell-pull settings from the first worked example:
- volume `225`
- target ETA `5`
- calculated rate about `2700`
- tracking enabled
- capacity `50`
- warning at `45`

### 3. Build the recipe

Now build the recipe in this exact order:

1. `OPENTRONS_PROTOCOL` for `Titration_5x5uL_step1`
2. `PUMP_HEXW2` withdraw `225 uL`
3. `SWV`
4. `OPENTRONS_PROTOCOL` for `Titration_5x5uL_step2`
5. `PUMP_HEXW2`
6. `SWV`
7. `OPENTRONS_PROTOCOL` for `Titration_5x5uL_step3`
8. `PUMP_HEXW2`
9. `SWV`
10. `OPENTRONS_PROTOCOL` for `Titration_5x5uL_step4`
11. `PUMP_HEXW2`
12. `SWV`
13. `OPENTRONS_PROTOCOL` for `Titration_5x5uL_step5`
14. `PUMP_HEXW2`
15. `SWV`

Why this order works:
- each Opentrons protocol performs exactly one titration addition and then finishes
- the queue naturally returns to the pump and SWV steps after each completed protocol
- the next protocol starts from its own explicit `starting_tip`, so tip usage stays predictable across the whole recipe

### 4. Sanity checks before running

Check these before you start:
- the Recipe Maker collection summary should still read about `1.125 mL`
- each Opentrons step points to the correct single-transfer protocol
- the five protocols use non-overlapping starting tips such as `A1`, `B1`, `C1`, `D1`, and `E1`
- each builder preview shows one transfer and no pause
- the OT-2 deck layout is identical across all five protocols

### 5. Send to queue and run

1. In `Recipes`, click `Send to Queue`.
2. Go to `Run Queue`.
3. Confirm the queue order matches the 15-item list above.
4. Make sure the pump is connected.
5. Make sure the OT-2 host/IP is correct.
6. Start the queue.

What physically happens:
- protocol 1 adds `5 uL` stock using its assigned starting tip and completes
- syringe withdraws `225 uL` into the flowcell
- SWV runs on that concentration
- protocol 2 runs with its own assigned starting tip
- the same pattern repeats until all 5 titration points are done

This version is useful when you want the recipe itself, rather than `pause` and `resume`, to be the thing coordinating the handoff between robot transfer, pump pull, and electrochemistry.

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
