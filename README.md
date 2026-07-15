# ByteWolf Robotics Platform

Safety-first digital twin platform for a PX4-powered multicopter.

## Current local environment

- Primary simulation environment: native Apple Silicon macOS.
- Flight controller: PX4 SITL v1.17.0.
- Simulator: Gazebo Harmonic.
- First city scene: Árpádföld–Mátyásföld, Budapest.

The Linux VM remains optional for future ROS 2 development; it is not needed to
run the native macOS PX4/Gazebo simulator.

## Run the Budapest simulator

```zsh
cd ~/bytewolf-robotics/PX4-Autopilot
source .venv/bin/activate
PX4_GZ_WORLD=budapest_arpadfold_matyasfold \\
CMAKE_PREFIX_PATH="$(brew --prefix qt@5)" \\
make px4_sitl gz_x500
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
altitude and distance limits before PX4 receives a command:

```zsh
.venv/bin/python -m brain.cli.fly_waypoint_land \\
  --north 5 --east 0 --takeoff-altitude 2 --waypoint-altitude 2
```
