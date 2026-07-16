# ByteWolf Robotics Platform

Safety-first digital twin platform for a PX4-powered multicopter.

## Current local environment

- Primary simulation environment: native Apple Silicon macOS.
- Flight controller: PX4 SITL v1.17.0.
- Simulator: Gazebo Harmonic.
- Baseline world: PX4's built-in `default` world.

The Linux VM remains optional for future ROS 2 development; it is not needed to
run the native macOS PX4/Gazebo simulator.

## Run the simulator

```zsh
./simulation/launch/validate_px4_gazebo.zsh
./simulation/launch/run_px4_gazebo.zsh base
```

The launcher keeps PX4's source tree untouched and accepts the official X500
sensor profiles: `vision`, `depth`, `mono-front`, `mono-down`, `lidar-down`,
`lidar-front`, and `lidar-2d`. To choose another installed Gazebo world, set
`PX4_GZ_WORLD`, for example:

```zsh
PX4_GZ_WORLD=empty ./simulation/launch/run_px4_gazebo.zsh base
```

## Set up the Python environment

```zsh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## Run the safety-core tests

Run the automated unit and adapter tests from the project's isolated Python
environment:

```zsh
.venv/bin/python -m unittest discover -s tests -v
```

These tests use fake MAVSDK/PX4 collaborators; they do not launch or validate
PX4 SITL and Gazebo. Run the mission commands below separately against a
running simulator for manual integration verification.

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
