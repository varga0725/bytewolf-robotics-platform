# ByteWolf Robotics Platform

Safety-first digital twin platform for a PX4-powered multicopter.

## Repository structure

```text
apps/        # API and dashboard applications
brain/       # Mission, safety, planning and perception domains
robots/      # Vehicle-specific integrations
simulation/  # Gazebo launchers, worlds, models and scenarios
shared/      # Cross-domain interfaces, schemas and configuration
tests/       # Automated verification
docs/        # Engineering documentation
docker/      # Container runtime surface
```

The current X500 V2 twin configuration is shared by the flight brain and the
simulator under `shared/config/x500v2`; PX4's third-party source tree remains
outside this structure at `PX4-Autopilot`.

## Current local environment

- Primary simulation environment: native Apple Silicon macOS.
- Flight controller: PX4 SITL v1.17.0.
- Simulator: Gazebo Harmonic (gz-sim 8.12.0).
- Baseline world: PX4's built-in `default` world.

The Linux VM remains optional for future ROS 2 development; it is not needed to
run the native macOS PX4/Gazebo simulator.

## Verify the simulation baseline

PX4 and Gazebo live outside this repository, so a drifted tree would quietly
change what a passing scenario means. `shared/config/x500v2/baseline.yaml` pins
the exact stack the committed evidence was produced against — PX4 and submodule
commits, the Gazebo release, and the sha256 of every PX4 file this platform
reads, renders fixtures from, or patches. Check a local environment against it:

```zsh
.venv/bin/python -m simulation.baseline
```

It changes nothing and exits non-zero on any drift.

PX4 v1.17.0 does not build on Apple Silicon macOS as released, so the checkout
needs the recorded patch set — C++17, Apple warning suppressions, the Homebrew
library path, and the shared-library suffix the optical flow build assumes:

```zsh
git -C PX4-Autopilot apply "$PWD/simulation/px4/macos-build.patch"
```

A patched tree reports itself as `v1.17.0-dirty`; that is the expected baseline,
not drift. Beyond applying that patch, nothing here modifies PX4's source tree.

## Run the simulator

```zsh
./simulation/gazebo/launch/validate_px4_gazebo.zsh
./simulation/gazebo/launch/run_px4_gazebo.zsh base
```

The launcher keeps PX4's source tree untouched and accepts the official X500
sensor profiles: `vision`, `depth`, `mono-front`, `mono-down`, `lidar-down`,
`lidar-front`, and `lidar-2d`. To choose another installed Gazebo world, set
`PX4_GZ_WORLD`, for example:

```zsh
PX4_GZ_WORLD=empty ./simulation/gazebo/launch/run_px4_gazebo.zsh base
```

## Set up the Python environment

```zsh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

`requirements.txt` installs the schema validator (`jsonschema`) required by the
mission, perception, and telemetry contracts. If the selected `python3` has no
`venv` module, create the environment with a full CPython installation instead,
for example `/opt/homebrew/bin/python3.13 -m venv .venv`, then run the same
install command.

## Run the safety-core tests

Run the automated unit and adapter tests from the project's isolated Python
environment:

```zsh
.venv/bin/python -m unittest discover -s tests -v
```

## Run a NIM Mission Agent proposal

The local `apps/gateway` NIM Mission Agent converts a Hungarian or English
request to a MissionSpec proposal. It has no direct MAVSDK/PX4 access; schema,
SafetyGate, and executable-shape checks must approve a proposal before the CLI
may connect to SITL. Configure `NVIDIA_API_KEY` and `NIM_MISSION_MODEL` in the
Git-ignored `.env` file. First create and review the plan:

```zsh
set -a; source .env; set +a
.venv/bin/python -m brain.cli.fly_nim_mission \
  --command "Szállj fel 2 méterre, lebegj 3 másodpercig, majd szállj le."
```

Then execute the printed plan path — never a newly generated proposal — against
a running simulator. Its sibling approval record pins the reviewed file's
SHA-256 hash, so an edited or unreviewed plan is refused before PX4 connects:

```zsh
.venv/bin/python -m brain.cli.fly_nim_mission \
  --mission-spec-file simulation/artifacts/agent-missions/<mission-id>.mission-spec.json \
  --execute
```

See [`docs/nim-mission-agent-v0_1.md`](docs/nim-mission-agent-v0_1.md) for the
supported shapes and the safety boundary.

## Talk to the Mission Agent from Telegram

The Telegram gateway is the conversational interface for SITL. It creates a
reviewed plan from a natural-language message, and requires a separate
`/execute <plan>` confirmation before it starts the existing safe execution
CLI. It never sends MAVLink/PX4 commands itself. Configure an explicit chat-ID
allowlist before starting it; see
[`docs/telegram-mission-gateway-v0_1.md`](docs/telegram-mission-gateway-v0_1.md).

## Dashboard Control Room

The dashboard is now served by the local FastAPI Command Gateway, shared with
the future mobile client. It combines live telemetry, camera evidence, a
Pi SDK/NVIDIA NIM conversational agent with durable local sessions, and
explicit mission approval; see [`apps/api/README.md`](apps/api/README.md) and
[`docs/pi-agent-v0_1.md`](docs/pi-agent-v0_1.md) for the local run command and
the safety boundary.

These tests use fake MAVSDK/PX4 collaborators; they do not launch or validate
PX4 SITL and Gazebo. Run the mission commands below separately against a
running simulator for manual integration verification.

## Run the headless P0 scenario matrix

The headless runner starts an isolated PX4/Gazebo instance, executes the three
nominal missions and an expected safety-rejection scenario, then tears down all
processes. Every scenario is assigned a dedicated artifact directory, recorded
in the JSON report.

```zsh
.venv/bin/python -m simulation.scenarios.scenarios
```

The unsafe-altitude scenario is a passing test only when the CLI rejects it
before a PX4 flight command. The nominal scenarios remain the inputs for the
9/10 repeatability gate. The runner can produce the aggregate proof directly:

```zsh
.venv/bin/python -m simulation.scenarios.scenarios --runs 10
```

## Run the expanded P0.v2 matrix

P0.v2 adds evidence-only boot/pre-arm, the exact 2 m / 10 s nominal profile,
a closed four-leg square, controlled HOLD/LAND interruptions, and an arm-before-
flight geofence rejection. Every MAVSDK scenario runs in its own fresh
PX4/Gazebo lifecycle; P0.v1 keeps its accepted shared-lifecycle behaviour.

```zsh
.venv/bin/python -m simulation.scenarios.scenarios --matrix-version p0.v2
```

The report identifies the outcome of each scenario. Treat its proof level
carefully:

- P0.v2 scenario reports are app+SITL evidence.
- Low battery is PX4/Gazebo fault-injection evidence: the matrix drains the real
  battery past the reserve mid-hover and the report records the parameters PX4
  confirmed holding. PX4 drains only while armed, so the arm reserve itself is
  not reachable this way.
- In-flight GNSS invalidity and missing telemetry stay unit/contract: PX4's
  `SIM_GZ_EN_GPS` is boot-time only, so GNSS cannot be dropped mid-flight.
- A stopped MAVSDK process cannot send a command; PX4's configured failsafe is
  the safety authority for that condition, and nothing injects it.

The P0.v2 matrix builds its own 3, 6, and 10 m/s wind fixtures, so a wind run
needs no manual setup; each report records the fixture it loaded. To inspect a
fixture by hand, generate one and hand all three of its parts to the launcher:

```zsh
.venv/bin/python -m simulation.gazebo.wind_profiles \
  --speed 6 \
  --source-world PX4-Autopilot/Tools/simulation/gz/worlds/windy.sdf \
  --output-world simulation/artifacts/wind/world.sdf \
  --source-models PX4-Autopilot/Tools/simulation/gz/models \
  --models-root simulation/artifacts/wind/models \
  --source-server-config PX4-Autopilot/Tools/simulation/gz/server.config \
  --output-server-config simulation/artifacts/wind/server.config

PX4_GZ_WORLD=windy \
  PX4_GZ_WORLD_FILE="$PWD/simulation/artifacts/wind/world.sdf" \
  PX4_GZ_MODELS="$PWD/simulation/artifacts/wind/models" \
  PX4_GZ_SERVER_CONFIG="$PWD/simulation/artifacts/wind/server.config" \
  ./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base
```

All three parts are required, and a wind world alone proves nothing. Gazebo
applies wind only to links that opt into it, and only when the `WindEffects`
system is loaded; PX4's stock X500 does neither, so its `windy` world exerts
exactly zero force. The fixture supplies the wind-enabled airframe
(`PX4_GZ_MODELS`) and the wind system (`PX4_GZ_SERVER_CONFIG`) as well as the
world. The wind force is scaled to the twin's `aerodynamics` drag rather than
Gazebo's default of 1.0, which drags the vehicle up to wind speed like a
balloon instead of modelling drag.

Do not label a wind or fault run as verified until its report records a
PX4/Gazebo execution using that exact fixture. A 10 m/s run additionally
reports that it extrapolates the drag model beyond its 2-9 m/s backing.

For the documented Apple Silicon nightly/manual gate, use:

```zsh
./simulation/gazebo/launch/run_p0_nightly.zsh
```

The resulting `p0-repeatability-*.json` reports the independent pass rate for
takeoff-hover-land, waypoint, and RTL. Each must meet the 90% threshold; the
safety-rejection scenario must pass on every run.

## Run the first flight mission

In a second terminal, while the PX4/Gazebo simulator is running, run the
bounded mission. The default is a 2 metre takeoff, 5 second hover,
and landing; the 20 metre safety ceiling is enforced before any PX4 command is
sent.

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land
```

For a different bounded test, pass explicit mission values. The active versioned
safety profile remains the upper bound and cannot be loosened by CLI flags:

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land \\
  --altitude 3 --hover-seconds 8
```

If the mission is interrupted after it starts, it still attempts to issue a
landing command before stopping. Allow the short mission to finish whenever
possible.

The mission layer creates high-level immutable commands only. PX4 remains
responsible for stabilization and motor control. Each completed mission returns
an immutable audit trail: `arming -> taking_off -> hovering -> landing -> completed`.
Every CLI invocation also writes a versioned `v0.2` JSON artifact. It records
the safety decision, terminal outcome, any failure reason, state transitions,
and the preflight snapshot (navigation/home/global-position readiness and
battery percentage when available). Use `--artifact-dir <directory>` to keep
the artifacts beside a particular test run.

## Waypoint foundation

The navigation layer now accepts a safety-validated relative waypoint (north,
east, target altitude). At execution time it converts that local target using
the drone's current GPS telemetry into the global coordinate format PX4 expects.

## Run a complete waypoint mission

With PX4 SITL already running, execute a small, safe test: take off to 2 m,
move 5 m north, hover for 3 seconds, then land.

```zsh
.venv/bin/python -m brain.cli.fly_waypoint_land
```

The target is configurable, but it is always validated against the explicit
altitude and distance limits before PX4 receives a command. Completion is only
reported once the GPS telemetry is within 1 m horizontally and vertically of the
target (or the mission times out and lands). An invalid in-flight GPS sample
(missing, non-finite, or out-of-range latitude, longitude, or altitude) is
rejected before it can produce a navigation command; if the vehicle is already
airborne, the mission performs its one bounded landing fallback:

```zsh
.venv/bin/python -m brain.cli.fly_waypoint_land \\
  --north 5 --east 0 --takeoff-altitude 2 --waypoint-altitude 2
```

## Run a Return-to-Home mission

With PX4 SITL already running, this asks PX4 to run its own return-to-launch
mode: take off to 2 m, hover for 3 seconds, return to the launch position, and
land. A successful execution audit ends in `completed` only after `in_air`
telemetry has observed flight and then landing. If that confirmation times out
or another RTL-stage error occurs, the adapter attempts a separate land command
and records the execution as failed rather than returning a successful audit.
The PX4 RTL altitude is explicitly set to the same safety-approved altitude as
the takeoff command.

```zsh
.venv/bin/python -m brain.cli.fly_return_to_home
```
