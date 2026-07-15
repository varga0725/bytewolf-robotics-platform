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

## Run the safety-core tests

```zsh
python3 -m unittest discover -s tests -v
```

## Run the first flight mission

In a second terminal, while the PX4/Gazebo simulator is running, create the
project's isolated Python environment once and install its dependency:

```zsh
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Then run the bounded mission. The default is a 2 metre takeoff, 5 second hover,
and landing; the 20 metre safety ceiling is enforced before any PX4 command is
sent.

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land
```

For a different bounded test, pass explicit values:

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land \\
  --altitude 3 --hover-seconds 8 --max-altitude 20
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
target (or the mission times out and lands):

```zsh
.venv/bin/python -m brain.cli.fly_waypoint_land \\
  --north 5 --east 0 --takeoff-altitude 2 --waypoint-altitude 2
```

## Run a Return-to-Home mission

With PX4 SITL already running, this validates PX4's own return-to-launch mode:
take off to 2 m, hover for 3 seconds, return to the launch position, and land.
Completion is reported only after the `in_air` telemetry confirms landing. If
the confirmation times out, the adapter sends a separate land command. The
PX4 RTL altitude is explicitly set to the same safety-approved altitude as the
takeoff command.

```zsh
.venv/bin/python -m brain.cli.fly_return_to_home
```
