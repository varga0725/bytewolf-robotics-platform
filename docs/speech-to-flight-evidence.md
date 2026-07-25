# Speech to flight — the whole chain, 2026-07-25

The first run in which every layer works together: a spoken request becomes a
simulated flight, through both human gates and without any of them being
shortened. Proof level: **app + PX4 SITL** — no physical aircraft.

## What ran

```
"Szállj fel két méterre, lebegj egy kicsit, és gyere vissza."
        │
        v
  transcript_v0_1        state=valid, confidence 0.95, language hu-HU
        │
        v
  command_suggestion     requires_approval: true   ← pinned by the schema
        │
        │  ── GATE 1: a human asked for a plan (--review) ──
        v
  NIM mission review     nvidia/nemotron-3-nano-30b-a3b
  + SafetyGate           proposed and safety-approved: TAKEOFF → HOLD → LAND
        │
        v
  MissionSpec 0.1        mission_id 03069d25-…, altitude 2 m, hold 3 s
        │
        │  ── GATE 2: a human approved that exact file (--execute) ──
        v
  PX4 SITL               arming → taking_off → hovering → landing → completed
        │
        v
  audit artifact         outcome: completed, safety_decision: approved
```

## What the gates actually did

Neither gate is decoration. Without `--review` the run stops after the
suggestion and prints that speech alone plans nothing. Without `--execute` the
reviewed plan sits on disk and the CLI tells the operator to approve that exact
file themselves. Both flags are typed by a person; nothing in the speech path can
supply them.

That is the same boundary the contract states and the dashboard already relies
on. Speech turned out to be one more way to *ask* — the review, the SafetyGate,
the approval and the executor are the ones the request had to pass, exactly as
they are for typed chat.

## What this does not show

- **No physical flight.** SITL is a simulator; nothing here says the aircraft
  behaves this way.
- **The request was typed, not spoken, in this run.** `--text` was used so the
  chain is reproducible without a microphone. The speech half is proven
  separately in `docs/speech-live-evidence.md`, on real engines; joining the two
  in one run needs a host with microphone permission.
- **One run.** A single successful pass is a wiring proof, not a reliability
  measurement, and the Speech Stack's "zero false mission starts" target needs
  adversarial attempts, not a happy path.
- **The model chose the mission.** A language model turned a sentence into
  TAKEOFF → HOLD → LAND. The SafetyGate validated the result against
  `twin.yaml`; it did not check that the plan matches what the person meant.

## Reproducing

```
./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base      # terminal 1

python -m brain.cli.speech_mission_demo \
    --text "Szállj fel két méterre, lebegj egy kicsit, és gyere vissza."   # gates closed
python -m brain.cli.speech_mission_demo --text "…" --review                # gate 1
python -m brain.cli.fly_nim_mission --mission-spec-file <plan> --execute    # gate 2
```

With `--audio <file>` the first step transcribes a recording instead, which is
the same chain with the speech half live.
