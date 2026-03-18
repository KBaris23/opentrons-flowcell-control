# Opentrons + PalmSens Parallel Execution Plan

This document captures the next-step architecture for running OT-2 liquid handling alongside PalmSens measurements without changing the current sequential queue behavior yet.

## Current state

- The queue is intentionally single-threaded and deterministic.
- PalmSens measurements run through the existing MethodSCRIPT runner.
- Pump steps and Opentrons protocol steps are now first-class queue items, but still execute one-at-a-time.

## Why not change it yet

- Parallel device work needs explicit stop, error, and completion contracts per device.
- A naive threaded queue would make experiment state, Slack status, file snapshots, and user stop behavior much harder to trust.
- PalmSens measurements are currently the most robust execution path, so they should remain the scheduling anchor until the robot side has equally clear lifecycle handling.

## Recommended next architecture

1. Introduce device runners as explicit jobs.
   - `PalmSensJob`
   - `PumpJob`
   - `OpentronsJob`

2. Add a scheduler layer above the queue.
   - The queue remains the authoring surface.
   - The scheduler decides whether the next item is blocking, parallel-safe, or a synchronization barrier.

3. Support synchronization primitives instead of implicit timing.
   - `wait_for_job`
   - `barrier`
   - `start_parallel_group`
   - `end_parallel_group`

4. Promote experiment resources into reservations.
   - PalmSens serial port reservation
   - OT-2 reservation
   - pump reservation
   - shared sample / deck / channel labels

5. Capture structured lifecycle events.
   - queued
   - starting
   - running
   - completed
   - failed
   - stopping
   - stopped

## Safest first increment

- Keep the visible queue exactly as-is.
- Add one new queue item type: `OPENTRONS_START_ASYNC`.
- Let it launch an `OpentronsJob` in the background and register a job id.
- Add a later `WAIT_FOR_JOB` step to rejoin before the next dependent PalmSens measurement.

This gives controlled overlap without forcing the whole queue to become fully parallel-aware all at once.

## Practical example

1. `OPENTRONS_START_ASYNC` - begin titration / dispense routine
2. `WAIT 10 s` - optional settling delay
3. `CV measurement` - PalmSens runs while OT-2 continues if safe
4. `WAIT_FOR_JOB opentrons_01` - ensure the robot is done
5. `PUMP_HEXW2` or next measurement

## Requirements before implementing parallel mode

- Reliable OT-2 execution backend, not just validation/simulation
- explicit stop / cancel support for robot jobs
- structured device status polling
- experiment-folder logging for asynchronous jobs
- UI status model that can show more than one active device at once

## Recommendation

Do not replace the current sequential queue. Add a scheduler beside it and introduce parallelism as an opt-in capability with explicit synchronization points.
