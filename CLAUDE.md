# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

A safety-first digital twin for a Holybro X500 V2 multicopter. Python mission/safety
layer → MAVSDK → PX4 SITL v1.17.0 → Gazebo Harmonic, running natively on Apple
Silicon macOS. `PX4-Autopilot` is a symlink to `~/bytewolf-robotics/PX4-Autopilot`
(the physical path must stay space-free — a PX4 subproject breaks on spaces in the
build path) and is git-ignored third-party source: never edit it.

## Commands

All Python runs through the project venv. The system Python lacks `jsonschema`/`PyYAML`
and will fail.

```zsh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

# Full suite (fake MAVSDK collaborators, no SITL needed; ~4 s)
.venv/bin/python -m unittest discover -s tests -v

# One module / one test
.venv/bin/python -m unittest tests.test_safety_profile -v
.venv/bin/python -m unittest tests.test_safety_profile.SafetyProfileTest.test_name

# Simulator (terminal 1), then a mission CLI (terminal 2)
./simulation/gazebo/launch/validate_px4_gazebo.zsh
./simulation/gazebo/launch/run_px4_gazebo.zsh base
.venv/bin/python -m brain.cli.fly_takeoff_hover_land

# Headless scenario matrices (start and tear down their own PX4/Gazebo)
.venv/bin/python -m simulation.scenarios.scenarios                        # P0.v1 smoke
.venv/bin/python -m simulation.scenarios.scenarios --runs 10              # repeatability gate
.venv/bin/python -m simulation.scenarios.scenarios --matrix-version p0.v2 # expanded matrix
./simulation/gazebo/launch/run_p0_nightly.zsh                             # nightly/manual gate

# Read-only summary of stored evidence
.venv/bin/python -m simulation.evidence
```

The headless launcher requires a prebuilt `px4_sitl_default/bin/px4`; build it once in
the PX4 checkout with `make px4_sitl gz_x500`. It runs PX4 in daemon mode (`-d`) on
purpose: the interactive `pxh>` prompt used to fill an unread output pipe and stall SITL
before MAVLink came up.

There is no lint, formatter, or CI config in this repo. `unittest` is the only gate.

## Non-negotiable safety architecture

These constraints are the point of the project — preserve them in every change.

- **PX4 owns stabilization and motor control.** This codebase only emits high-level,
  immutable commands (arm/takeoff/goto/land/RTL). Nothing here enters the real-time loop.
- **`shared/config/x500v2/twin.yaml` is the single source of the safety contract**
  (20 m altitude, 3 m/s, 50 m radius, geofence polygon, 40% arm battery). Loaded via
  `brain/safety/profile.py`. CLI flags may only tighten it, never loosen it. Adding a
  second source of a limit is a design regression.
- **`brain/safety/gate.py` runs before any adapter call.** A rejected mission must never
  reach MAVSDK. Non-finite values (NaN, ±∞) are rejected, not clamped.
- **Actuation is never retried.** Only telemetry reads retry. After an *airborne* failure
  exactly one land fallback is permitted (`runtime_policy.v0_1.yaml`,
  `_fallback_land_after_airborne_failure`). Two land attempts is a bug.
- **Fail-closed on telemetry.** Missing/invalid health, home, global position, or battery
  means no arm. Invalid in-flight GNSS may not become a navigation command.
- **The MAVSDK client cannot act after it dies.** A stopped process commands nothing;
  PX4's own failsafe is the authority for that case. Never claim app-side coverage for it.
- **Telemetry paths are read-only.** The dashboard (`apps/dashboard/`) and the ROS 2
  bridge (`robots/drone/x500v2/ros2/`) have no control endpoint or topic — keep it that way.

## Layout and flow

```
brain/safety/        gate.py (deterministic validation) + profile.py (twin.yaml loader)
brain/mission/       flight.py mission types · execution.py immutable audit trail
                     runtime_policy.py + runtime_watchdog.py (live battery/GNSS guard)
                     artifacts.py versioned v0.2 JSON audit artifacts
brain/mission_spec/  MissionSpec v0.1 validation + compiler → orchestrator (safe bridge)
brain/adapters/      mavsdk_adapter.py — the only place that talks to PX4
brain/navigation/    relative north/east waypoint → global GPS target conversion
brain/telemetry/     ROS-independent domain events, contract loader, dashboard relay
brain/cli/           one module per bounded mission; each writes an audit artifact
robots/drone/x500v2/ optional ROS 2 Humble bridge (lazy rclpy import; no-op on macOS)
apps/dashboard/      read-only local telemetry viewer
simulation/          gazebo/launch/*.zsh · scenarios/scenarios.py runner · evidence.py
shared/              config/x500v2 (twin, runtime policy, bridge contracts) + JSON schemas
```

Mission path: CLI → SafetyGate → MAVSDK adapter preflight → PX4. Every phase transition
lands in an immutable `MissionExecution`
(`arming → taking_off → hovering → landing → completed`) and is persisted as a `v0.2`
artifact. Each CLI takes `--artifact-dir`; scenario runs get one directory per scenario
and a unique MAVSDK gRPC port (51000–60999) — the shared default `50051` caused
cross-scenario connection timeouts.

Adding a mission means touching all of: a type in `brain/mission/flight.py`, gate
validation, an adapter method, a CLI module, and a `Scenario` entry in
`simulation/scenarios/scenarios.py`.

## Evidence discipline

The project treats simulation reports as proof, and the proof level is part of the claim:

- **unit/contract** — fake MAVSDK; covers low battery, GNSS invalidity, telemetry loss.
- **app+SITL** — a real headless PX4/Gazebo run recorded in
  `simulation/artifacts/headless/p0-*.json`.
- **PX4/Gazebo fault-injection** — only when the run actually loaded the fixture.

Never label a wind or fault run verified unless its report records a PX4/Gazebo execution
with that exact fixture. Wind worlds must be generated first
(`simulation.gazebo.wind_profiles`) and passed via `PX4_GZ_WORLD_FILE`. The P0 gate needs
9/10 nominal scenarios and 100% on the safety-rejection scenarios.

## Notion is the source of truth for status

The project lives in Notion under **ByteWolf Robotics Platform — Drone Digital Twin**
(project page in the Projects DB, with a linked task board and per-task pages such as
"P0.v2 — Kibővített flight safety regressziós mátrix" and "P1 — ROS 2 telemetry bridge").
Roadmap state, gate closures, and evidence file names are recorded there, and later
sections deliberately override earlier ones. Read the current project page before
claiming a phase is done, and record status changes back to it with the commit hashes and
artifact paths that prove them. Notion pages are written in Hungarian; code and docstrings
are English.

Current state: P0 closed (10/10 repeatability). P0.v2 at 6/8 — open: real 3/6/10 m/s wind
world runs. P1 locally complete; the Ubuntu 22.04 + ROS 2 Humble topic smoke is deferred
for lack of an environment.
