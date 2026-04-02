# Opentrons Flowcell Console Software Manual

## Document Purpose

This manual explains how to use the Opentrons Flowcell Console application for electrochemical measurements, Chemyx syringe pump control, Opentrons OT-2 protocol handling, recipe building, queue execution, and plotting.

This version is written so it can be pasted directly into a Notion page. It also marks where screenshots and setup photos should be added.

---

## 1. System Overview

The Opentrons Flowcell Console is a desktop GUI that combines several workflows in one application:

- Session and experiment recordkeeping
- Electrochemical method creation for CV and SWV
- Loading and running custom MethodSCRIPT files
- Chemyx syringe pump control
- Opentrons OT-2 protocol inspection, generation, and queue integration
- Recipe building from reusable steps
- Queue-based execution of pump, measurement, and robot actions
- Live and offline plotting of measurement CSV files

The software saves measurement data under `measurement_data/`, methods under `methods/library/`, and bundled OT-2 protocols under `opentrons_protocols/`.

`[Insert Screenshot: Full application window after starting a session]`

---

## 2. Before You Start

### 2.1 Hardware and Software Needed

Depending on your workflow, you may use some or all of the following:

- A Windows PC running Python and this application
- A PalmSens-compatible electrochemistry device connected by USB/serial
- A Chemyx Fusion syringe pump connected by serial
- An Opentrons OT-2 on the local network
- A flowcell and tubing setup
- A collection syringe / waste syringe

### 2.2 Important Behavior

- Most tabs are hidden until a session is started.
- Measurements require both a session and an experiment.
- The `Fluidics` tab is only shown if the pump backend is available.
- The Opentrons simulation mode only works if the local `opentrons` Python package is installed.
- Collection syringe state is persistent across app restarts, so tracked collected volume is remembered until manually reset.

### 2.3 Recommended Setup Photos

Add real photos of your bench setup here. These are more useful than screenshots because they help a new operator reproduce the physical arrangement.

`[Insert Photo: Overall bench setup with PC, pump, potentiostat, OT-2, and flowcell]`

`[Insert Photo: Flowcell and tubing connections labeled inlet/outlet]`

`[Insert Photo: Chemyx pump with syringe installed]`

`[Insert Photo: Collection syringe / waste bottle arrangement]`

`[Insert Photo: OT-2 deck setup with racks/tiprack locations visible]`

`[Insert Photo: USB/serial connections to pump and measurement device]`

---

## 3. Recommended Operating Workflow

For a normal experiment, use this order:

1. Start a session.
2. Start an experiment.
3. Check hardware connections.
4. Create or select methods.
5. Create pump steps and/or OT-2 protocol steps.
6. Build a recipe if needed.
7. Send items to the queue.
8. Review the queue carefully.
9. Run the queue.
10. Monitor the live log, pump state, OT-2 state, and plots.
11. End the experiment.
12. End the session.

`[Insert Screenshot: Bottom Session/Experiment bar with an active session and experiment]`

---

## 4. Main Window Layout

The application is organized into top-level tabs:

- `Fluidics`
- `Methods`
- `Opentrons`
- `Script`
- `Run Queue`
- `Recipes`
- `Plotter`

There is also a bottom `Session / Experiment` bar that controls data organization and metadata.

---

## 5. Session and Experiment Bar

The session/experiment controls are at the bottom of the window. This should usually be the first thing the operator uses.

### 5.1 Session Section

Fields:

- `Session Name`: name for the full working session
- `User`: operator name
- `Chip ID`: chip identifier
- `Notes`: general session notes

Buttons:

- `Start Session`: creates a new session folder under `measurement_data/`
- `End Session`: closes the session
- `Choose Session`: opens an existing session folder
- `Update Session Metadata`: updates the saved session metadata without closing the session

What this does:

- Creates a session folder with metadata and a session log
- Unlocks the main workflow tabs
- Allows experiments to be created inside the active session

### 5.2 Experiment Section

Fields:

- `Experiment Name`: name of the current experiment
- `Notes`: experiment-specific notes

Buttons:

- `Start Experiment`: creates an experiment folder inside the active session
- `End Experiment`: closes the experiment

What this does:

- Measurements are saved inside the active experiment folder
- Queue-based measurements should not be run before the experiment is started

### 5.3 Status Line

The blue status line shows the currently active session and experiment.

### 5.4 Screenshot Needed

`[Insert Screenshot: Session/Experiment bar with all fields labeled]`

---

## 6. Fluidics Tab

The `Fluidics` tab is for direct control of the Chemyx syringe pump and for adding pump actions to the queue.

`[Insert Screenshot: Fluidics tab full view]`

### 6.1 Connection (Chemyx)

Controls:

- `Simulate (no hardware)`: lets you test pump actions without a connected pump
- `Port`: COM port selection
- `Refresh`: refresh detected ports
- `Baud`: serial baud rate
- `EOL`: line ending setting (`cr`, `lf`, `crlf`)
- `Connect`: connect to the selected serial port
- `Auto-connect`: try the default / detected port automatically
- `Disconnect`: disconnect from the pump

Status:

- Connection label shows `Disconnected` or current state

Use this section to establish communication before sending pump commands.

`[Insert Screenshot: Fluidics tab - Connection area]`

### 6.2 Parameters

Fields and options:

- `Units`: `mLmin`, `mLhr`, `uLmin`, `uLhr`
- `Syringe preset`: typical syringe size presets
- `Diameter (mm)`: syringe inner diameter
- `Mode`: `infuse` or `withdraw`
- `Rate`: pump rate
- `Volume`: target volume

Checkboxes:

- `Auto status-port before Apply/Run`: checks status before apply/run commands
- `Poll status during run`: monitors run status during motion

Buttons:

- `Apply`: sends the selected parameters to the pump
- `Run (hexw2)`: applies the run logic for the main pump motion command
- `Queue Apply`: adds an `APPLY` step to the queue
- `Queue Run`: adds a `HEXW2` run step to the queue

Status:

- `Run: idle` or a live estimate of run progress

Important note:

- `Apply` does not move liquid. It only sets parameters.
- `Run (hexw2)` is the actual motion command for the normal queued flowcell pull behavior.

`[Insert Screenshot: Fluidics tab - Parameters area]`

### 6.3 Controls

Buttons:

- `Start`: sends the pump start command
- `Pause`: pauses pump motion
- `Stop`: stops pump motion
- `Restart`: restarts / resets pump state
- `Pump Status`: asks the pump for status
- `Port Status`: checks serial/port-level status
- `Reset Syringe State`: resets tracked collection volume to 0 mL
- `Queue Reset Step`: adds a syringe-state reset step to the queue

Use these buttons for direct intervention during setup, troubleshooting, or manual operation.

`[Insert Screenshot: Fluidics tab - Controls area]`

### 6.4 Raw Command

Fields:

- `Command`: free-text pump command

Buttons:

- `Send to Pump`: sends the raw command immediately
- `Add to Queue`: adds the raw command as a queue step

Use this only if you know the Chemyx Basic Mode command syntax.

### 6.5 Syringe Registry

Displays:

- Total tracked collected volume
- Number of tracked collection steps
- Capacity and warning threshold
- Last registry event and update time

Purpose:

- Helps prevent overfilling the collection syringe
- Persists across app restarts

`[Insert Screenshot: Fluidics tab - Syringe Registry and log]`

### 6.6 Best Practice

Use `Reset Syringe State` only after the collection syringe has been physically emptied.

---

## 7. Methods Tab

The `Methods` tab is where electrochemical measurements are defined and added to the queue.

`[Insert Screenshot: Methods tab overview]`

### 7.1 Technique Selection Buttons

Buttons:

- `Cyclic Voltammetry (CV)`: opens the CV parameter form
- `Square Wave Voltammetry (SWV)`: opens the SWV parameter form
- `Custom Script (File)`: loads an existing MethodSCRIPT file
- `PStrace SWV Preset`: generates and immediately runs a built-in SWV preset
- `Pause / Alert`: creates queue pauses or alert pauses

`[Insert Screenshot: Methods tab - Technique selection panel]`

### 7.2 Device Connection Area

Controls:

- `Check Device Connection`: scans for available serial measurement devices
- `Device port`: selected serial device or auto-detect
- `Refresh`: refreshes detected device ports

Status:

- Shows number of detected devices and selected device

Use this before running measurements.

### 7.3 Execution Options

Options:

- `Save raw packets`: saves low-level raw communication packets
- `Simulate measurements (no device)`: runs measurements without hardware
- `Delay between steps (s)`: delay between queued measurement steps

This affects how the queue and immediate method runs behave.

`[Insert Screenshot: Methods tab - Device and execution options]`

### 7.4 CV Form

Fields:

- `Begin Potential (V)`
- `Vertex 1 (V)`
- `Vertex 2 (V)`
- `Step Potential (V)`
- `Scan Rate (V/s)`
- `Number of Scans`
- `Conditioning Potential (V)`
- `Conditioning Time (s)`
- `MUX16 Channels (1-16, 0=off)`
- `Library note (optional)`

Buttons:

- `Generate Script`: writes the CV MethodSCRIPT preview to the `Script` tab
- `Run Now`: runs the CV immediately
- `Add to Queue`: stores the method and adds it to the queue

Notes:

- If multiple MUX channels are entered, multiple queue items may be created
- MUX input supports ranges such as `1-4,7,10`

`[Insert Screenshot: Methods tab - CV form]`

### 7.5 SWV Form

Fields:

- `Begin Potential (V)`
- `End Potential (V)`
- `Step Potential (V)`
- `Amplitude (V)`
- `Frequency (Hz)`
- `Number of Scans`
- `Delay Between Scans (s)`
- `Conditioning Potential (V)`
- `Conditioning Time (s)`
- `MUX16 Channels (1-16, 0=off)`
- `Library note (optional)`

Buttons:

- `Generate Script`
- `Run Now`
- `Add to Queue`

Notes:

- `Number of Scans` and `Delay Between Scans` can generate repeated SWV runs
- If MUX channels are also used, the queue can expand into multiple channel/scan combinations

`[Insert Screenshot: Methods tab - SWV form]`

### 7.6 Custom Script (File)

Fields:

- `MethodSCRIPT file`
- `MUX16 Channels`
- `Library note (optional)`

Buttons:

- `Browse...`: select a `.ms` or `.txt` MethodSCRIPT file
- `Run Now`
- `Add to Queue`

Behavior:

- If the loaded script already contains a MUX header, the GUI warns about it
- The script contents are shown in the `Script` tab after loading

`[Insert Screenshot: Methods tab - Custom Script panel]`

### 7.7 Pause / Alert

Fields:

- `Pause Time (sec)`
- `Alert Message`

Buttons:

- `Add Pause to Queue`: adds a timed pause
- `Add Alert Pause`: adds a pop-up alert/pause step
- `Run Pause Now`: starts a local timer immediately

Use this when manual intervention is needed between steps.

`[Insert Screenshot: Methods tab - Pause/Alert panel]`

### 7.8 PStrace SWV Preset

The `PStrace SWV Preset` button inserts a hard-coded preset script and runs it immediately. This is best treated as a convenience or training shortcut, not a general recipe-building tool.

---

## 8. Opentrons Tab

The `Opentrons` tab supports both file-based OT-2 workflows and a UI-based protocol builder.

`[Insert Screenshot: Opentrons tab overview]`

### 8.1 Protocol Config Subtab

This subtab is for working with saved or bundled OT-2 Python protocol files.

#### Protocol Summary

Displays:

- Name
- API level
- Robot type
- Author
- Description
- Warnings

Use `Inspect` to populate and refresh this summary.

`[Insert Screenshot: Opentrons - Protocol Summary]`

#### Saved / Bundled Protocols

Fields:

- `Available protocol`: dropdown of bundled / library protocols
- `Protocol file`: path to the selected file
- `Run mode`: `Validate Only`, `Simulate (SDK required)`, or `Run on OT-2 (HTTP API)`
- `Robot host/IP`
- `Robot API port`

Buttons:

- `Refresh`: reload protocol files from the protocol directory
- `Browse`: choose a Python protocol file manually
- `Check Connectivity`: ping the OT-2 host
- `Inspect`: parse the selected protocol and show metadata/warnings
- `Run Now`: execute the protocol immediately
- `Add to Queue`: add the protocol to the queue
- `Add Resume`: add a queue step that resumes a paused OT-2 run
- `Add Home`: add a queue step that homes the OT-2
- `Home OT-2`: send an immediate home command
- `Load Into Builder`: loads a saved generated builder protocol back into the builder
- `Delete From Library`: removes a saved generated library protocol

Use cases:

- Running an existing OT-2 script
- Queueing a protocol start / resume / home action
- Checking robot network reachability

`[Insert Screenshot: Opentrons - Protocol Config controls]`

### 8.2 Protocol Builder Subtab

This subtab creates OT-2 protocols directly in the GUI.

#### Setup -> Protocol Metadata

Fields:

- `Name`
- `Author`
- `Run mode`
- `Description`
- `API level`
- `Robot`
- `Pipette`
- `Pipette side`
- `Tiprack alias`
- `Starting tip`

Displays:

- `Tip budget`: estimated tip usage from the chosen starting tip to the end of the rack

Buttons:

- `Preview`
- `Run Now`
- `Add to Queue`
- `Add Resume`
- `Save to Library`
- `Load Selected File`
- `Clear Builder`

Use this section to define the protocol-level metadata and pipette setup.

`[Insert Screenshot: Opentrons Builder - Setup tab]`

#### Setup -> Deck Labware

Fields:

- `Alias`
- `Load name`
- `Slot`

Buttons:

- `Add / Update`: adds or updates a labware row
- `Delete`: removes the selected labware row

Purpose:

- Maps short aliases like `tips`, `stock`, or `dilute` to real Opentrons labware definitions and deck slots

`[Insert Screenshot: Opentrons Builder - Deck Labware panel]`

#### Steps -> Step Builder

Supported step kinds:

- `transfer`
- `move_to`
- `aspirate`
- `dispense`
- `blow_out`
- `delay`
- `pick_up_tip`
- `drop_tip`
- `home`
- `comment`
- `pause`

Fields:

- `Kind`
- `Volume (uL)`
- `Source Alias`
- `Source Well`
- `Dest Alias`
- `Dest Well`
- `Location`
- `New Tip`
- `Delay (s)`
- `Text / Message`

Buttons:

- `Add Step`
- `Update Selected`
- `Delete Step`
- `Clear Steps`

Additional buttons below the step list:

- `Preview`
- `Run Now`
- `Add to Queue`
- `Add Resume`
- `Save to Library`
- `Copy`
- `Paste After`
- `Duplicate`

Use this section to build a structured OT-2 protocol without editing Python directly.

`[Insert Screenshot: Opentrons Builder - Step Builder]`

#### Generated Preview

This subtab shows the generated Python protocol source.

Buttons:

- `Preview`
- `Run Now`
- `Add to Queue`
- `Add Resume`
- `Save to Library`
- `Load Selected File`

Use this view to verify the final generated script before running or saving it.

`[Insert Screenshot: Opentrons Builder - Generated Preview]`

### 8.3 Good Practice for OT-2 Use

- Always inspect or preview a protocol before running it on the robot
- Verify deck slots and labware names carefully
- Check connectivity before robot mode runs
- Use `Add Resume` only with pause-capable protocols
- Use `Home OT-2` or `Add Home` as recovery steps when needed

---

## 9. Script Tab

The `Script` tab is a plain-text preview of the last generated MethodSCRIPT.

What it does:

- Displays generated CV/SWV scripts
- Displays loaded custom MethodSCRIPT files
- Lets the operator inspect the exact script content

There are no action buttons in this tab.

`[Insert Screenshot: Script tab showing a generated method]`

---

## 10. Run Queue Tab

The `Run Queue` tab is the execution center of the application.

`[Insert Screenshot: Run Queue tab full view]`

### 10.1 Top Control Bar

Buttons:

- `Run Queue`: runs the full queue from the top
- `From Selected`: starts from the selected queue item
- `Stop`: stops queue execution and tries to stop pump and OT-2 activity
- `Save`: saves the queue as JSON
- `Load`: loads a saved queue JSON file
- `Copy`: copies selected queue items
- `Paste`: pastes copied items after the selection
- `Duplicate`: duplicates selected queue items
- `Delete`: removes selected queue items
- `Confirm Move`: confirms drag-and-drop reorder changes
- `Clear All`: clears the queue

Important note:

- Reordering is drag-and-drop, but the new order is only finalized after `Confirm Move`.

`[Insert Screenshot: Run Queue - top control bar]`

### 10.2 Queue Table

Columns:

- `#`
- `Type`
- `Status`
- `Details`

Behavior:

- Double-click editable pump/pause items to edit them
- Right-click for edit/copy/paste/duplicate/delete actions
- Queue items change status during execution

Possible step types include:

- `CV`
- `SWV`
- `PAUSE`
- `ALERT`
- `PUMP_*`
- `OPENTRONS_*`

`[Insert Screenshot: Run Queue - queue table with example items]`

### 10.3 Live Output Log

This panel shows run-time messages from:

- Queue execution
- Measurement runs
- Pump actions
- OT-2 actions
- Session logging

Use this as the main troubleshooting view during execution.

`[Insert Screenshot: Run Queue - live log panel]`

### 10.4 Bottom Information Bar

Displays:

- `Measurements this session`
- `Script registry`
- `Collection`
- `Registry` warning status

Buttons:

- `Reset Counter`: resets the measurement counter
- `Reset Syringe State`: resets persistent tracked collection volume
- `Clear Registry`: clears the in-memory method registry

`[Insert Screenshot: Run Queue - bottom info bar]`

### 10.5 Queue Execution Notes

- Measurement runs require an active experiment
- `PAUSE` waits for a time interval
- `ALERT` pauses for user acknowledgement
- `PUMP_APPLY` sets pump parameters only
- `PUMP_HEXW2` is the normal queued flow/motion step
- `PUMP_STATE_RESET` resets persistent syringe tracking
- `OPENTRONS_PROTOCOL` starts or validates/simulates a protocol
- `OPENTRONS_RESUME` resumes a paused OT-2 run
- `OPENTRONS_HOME` homes the OT-2

### 10.6 Save / Load Queue

Use this when you want to preserve a run plan and reuse it later.

Recommended screenshot:

`[Insert Screenshot: Example saved queue loaded into Run Queue]`

---

## 11. Recipes Tab

The `Recipes` tab is a composition tool. It does not execute runs directly. Instead, it builds reusable sequences that can later be sent into the queue.

`[Insert Screenshot: Recipes tab overview]`

### 11.1 Top Recipe Control Bar

Buttons:

- `Add Pump Step`
- `Add Method Step`
- `Move Up`
- `Move Down`
- `Copy`
- `Paste`
- `Duplicate`
- `Delete`
- `Save`
- `Load`
- `Clear`
- `Send to Queue`

Display:

- `Collection plan`: estimated number of tracked collection steps and total planned volume

Use this section to manage the recipe as a list before sending it to the queue.

`[Insert Screenshot: Recipes - top control bar and collection plan]`

### 11.2 Recipe Table

Columns:

- `#`
- `Type`
- `Block`
- `Details`

Color legend:

- Green: voltammetry steps
- Yellow: block steps
- Red: alert/pause
- Gray: other

Behavior:

- Double-click editable steps to open an editor
- Right-click for copy/paste/duplicate/select-range/delete

`[Insert Screenshot: Recipes - recipe table with legend]`

### 11.3 Pump Steps Subtab

This subtab creates pump-related recipe steps.

Fields:

- `Pump action`
- `Units`
- `Mode`
- `Diameter (mm) (syringe ID)`
- `Syringe preset`
- `Calculated rate`
- `Volume`
- `Delay (min)`
- `Wait (sec)`
- `Target ETA (s)`
- `Track collected volume`
- `Collection syringe`
- `Raw cmd`
- `Alert message`

Buttons:

- `Preset Flowcell Pull`: fills in a standard flowcell-pull setup
- `Add Syringe State Reset`: adds a reset step for persistent collection tracking

Common pump actions:

- `HEXW2`
- `APPLY`
- `COMMAND`
- `START`
- `PAUSE`
- `STOP`
- `RESTART`
- `STATUS`
- `STATUS_PORT`
- `STATE_RESET`
- `WAIT`
- `ALERT`

Important notes:

- `Track collected volume` should be enabled when a step physically adds liquid to the collection syringe
- `Preset Flowcell Pull` is the fastest way to configure the standard withdraw step
- Only fields relevant to the selected action are used

`[Insert Screenshot: Recipes - Pump Steps subtab]`

### 11.4 Method Library Subtab

This subtab pulls from the saved method library rather than creating new methods from scratch.

Controls:

- `Search`
- `Technique` filter
- `View` filter (`ALL`, `BASE`, `MUX`)
- `Refresh`

Channel sweep controls:

- `Sweep Start`
- `End`
- `Step`
- `Reverse`
- `Repeats/ch`
- `Custom order`
- `Add Channel Sweep Block`

Table columns:

- `Hash`
- `Note`
- `Technique`
- `Params`

Use this subtab to add previously saved methods or generate a multi-channel sweep block.

`[Insert Screenshot: Recipes - Method Library subtab]`

### 11.5 Opentrons Subtab

This subtab inserts OT-2-related recipe steps.

Fields:

- `Protocol`
- `Path`
- `Run mode`
- `Protocol name`
- `Robot host/IP`
- `Robot API port`

Buttons:

- `Refresh`
- `Browse`
- `Add Protocol Step`
- `Add Resume Step`
- `Add Home Step`

Use this when a recipe should start a robot protocol, resume a paused robot protocol, or home the OT-2 later in the workflow.

`[Insert Screenshot: Recipes - Opentrons subtab]`

### 11.6 Blocks Subtab

This subtab inserts predefined multi-step blocks from JSON files.

Controls:

- `Refresh Blocks`
- `Add Block`
- `View` filter (`All`, `Default`, `Custom`, `Saved`)

Table columns:

- `Block`
- `Items`

Current default blocks include:

- `chemyx_apply_defaults.json`
- `chemyx_hexw2_infuse_25ul.json`
- `chemyx_withdraw_225ul.json`
- `syringe_state_reset.json`
- `wait_10s.json`

Current saved recipe examples include:

- `flowcell_cleaning_di.json`
- `max training.json`
- `titration_5x5ul.json`
- `waste_disposal_reset.json`

Use blocks to standardize common procedures and reduce manual editing.

`[Insert Screenshot: Recipes - Blocks subtab]`

### 11.7 Saving and Loading Recipes

- `Save` writes the recipe to JSON
- `Load` reads a recipe JSON file back into the editor
- `Send to Queue` copies the current recipe items into the Run Queue tab

Recommended screenshot:

`[Insert Screenshot: Example completed recipe before Send to Queue]`

---

## 12. Plotter Tab

The `Plotter` tab is used for both live measurement plots and offline CSV review.

`[Insert Screenshot: Plotter tab overview]`

### 12.1 Controls

Buttons:

- `Load and Plot CSV`: loads one or more CSV files
- `Clear Plot`: clears the figure
- `Legend`: shows or hides the legend

Options:

- `Overlay`: determines whether new traces are added to the existing plot or replace it
- `Plot Y`: selects which current series to show

Available Y-series options:

- `Auto`
- `Current (uA)`
- `Current Forward (uA)`
- `Current Reverse (uA)`
- `Current Diff (uA)`

### 12.2 Plot Area

The plot displays:

- X-axis: potential
- Y-axis: current

It supports:

- Live plotting during active measurements
- Overlay of multiple measurements
- Legend dragging
- Offline re-plotting from saved CSV files

`[Insert Screenshot: Plotter with overlaid CSV traces]`

### 12.3 Typical Use

- During a measurement, the plot updates live automatically
- After a measurement, you can reload and compare CSV files manually

---

## 13. Files and Data Locations

The software uses these main locations:

- `measurement_data/`: sessions, experiments, logs, and measurement CSV outputs
- `methods/library/`: saved MethodSCRIPT files
- `methods/library_map.json`: method library index
- `opentrons_protocols/`: bundled and user-saved OT-2 Python protocols
- `recipe_maker/default_blocks/`: default reusable blocks
- `recipe_maker/saved_recipes/`: example or saved recipes

Recommended screenshot:

`[Insert Screenshot: Windows Explorer view of measurement_data session and experiment folders]`

---

## 14. Suggested Section for Manual Photos and Figures

Use this exact media plan when assembling the Notion page.

### 14.1 Setup Photos

- Figure 1: Overall bench setup
- Figure 2: Flowcell and tubing close-up
- Figure 3: Chemyx pump with syringe installed
- Figure 4: Collection syringe / waste arrangement
- Figure 5: OT-2 deck layout with loaded labware
- Figure 6: Serial / USB / network connections

### 14.2 Required Screenshots

- Screenshot 1: Full application after starting a session
- Screenshot 2: Session/Experiment bar
- Screenshot 3: Fluidics tab full view
- Screenshot 4: Fluidics connection section
- Screenshot 5: Fluidics parameters section
- Screenshot 6: Fluidics controls and syringe registry
- Screenshot 7: Methods tab overview
- Screenshot 8: Methods CV form
- Screenshot 9: Methods SWV form
- Screenshot 10: Methods custom script panel
- Screenshot 11: Methods pause/alert panel
- Screenshot 12: Opentrons Protocol Config
- Screenshot 13: Opentrons Protocol Builder setup
- Screenshot 14: Opentrons Protocol Builder steps
- Screenshot 15: Opentrons Generated Preview
- Screenshot 16: Script tab
- Screenshot 17: Run Queue full view
- Screenshot 18: Run Queue with a realistic queue loaded
- Screenshot 19: Run Queue live log / collection status
- Screenshot 20: Recipes tab overview
- Screenshot 21: Recipes Pump Steps subtab
- Screenshot 22: Recipes Method Library subtab
- Screenshot 23: Recipes Opentrons subtab
- Screenshot 24: Recipes Blocks subtab
- Screenshot 25: Example completed recipe
- Screenshot 26: Plotter tab with example data
- Screenshot 27: File/folder output structure in Windows Explorer

---

## 15. Quick Start Summary for New Users

If the reader only needs the shortest operating summary, use this:

1. Start a session.
2. Start an experiment.
3. Confirm the measurement device, pump, and OT-2 are connected.
4. Create methods in `Methods` or load them from `Recipes -> Method Library`.
5. Build pump and robot steps as needed.
6. Send everything to `Run Queue`.
7. Review the order carefully.
8. Run the queue.
9. Watch the log and plot.
10. Reset syringe state only after physically emptying the collection syringe.
11. End the experiment, then end the session.

---

## 16. Operator Notes / Local Customization

This section should be customized with your lab-specific details:

- Which COM port is normally used for the potentiostat
- Which COM port is normally used for the Chemyx pump
- The usual syringe size and diameter
- The normal flowcell pull volume and target time
- The OT-2 IP address used in your lab
- The standard OT-2 deck layout
- The standard chip / flowcell priming procedure
- Any safety or cleanup requirements before ending a run

`[Insert Photo or Screenshot: Your lab-standard OT-2 deck map]`

`[Insert Photo: Your lab-standard tubing and flow direction labels]`

---

## 17. Troubleshooting Notes

### No tabs are visible except the bottom bar

Cause:

- No active session

Action:

- Start a session first

### Measurements will not run

Cause:

- No active experiment
- Device not connected
- Wrong device port

Action:

- Start an experiment
- Use `Check Device Connection`
- Confirm the selected device port

### Pump commands do not move liquid

Cause:

- Only `Apply` was sent

Action:

- Use `Run (hexw2)`, `Queue Run`, or a queue step with `PUMP_HEXW2`

### Collection volume warning appears

Cause:

- Tracked collected volume reached warning threshold

Action:

- Empty the collection syringe physically, then run `Reset Syringe State`

### OT-2 protocol will not run

Cause:

- Wrong host/IP
- Robot unreachable
- Invalid protocol file

Action:

- Use `Check Connectivity`
- Use `Inspect`
- Verify deck layout and run mode

### Plot is blank

Cause:

- CSV missing expected columns
- Wrong Y-series selected

Action:

- Load a valid measurement CSV
- Try `Plot Y -> Auto`

---

## 18. Version Reference

This manual is based on the current GUI structure in this repository version:

- Application title: `Opentrons Flowcell Console`
- Version constant in code: `1.0.0`

