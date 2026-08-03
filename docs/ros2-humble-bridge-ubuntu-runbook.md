# ROS 2 Humble telemetry bridge — Ubuntu runbook

How to stand up the optional telemetry-only ROS 2 bridge beside PX4 SITL on an
Ubuntu 22.04 host, and how to tell whether it actually relayed anything.

This is a second host, not a replacement. macOS remains a supported development
environment and every script here keeps working there.

## Why Ubuntu 22.04 specifically

ROS 2 Humble is the distribution this project's bridge contract targets, and
22.04 is where Humble is native. That choice forces the rest of this page.

## The two interpreters

This is the part that surprises people, so it comes first.

| | Python | Why |
| --- | --- | --- |
| Platform (`.venv`) | 3.12 | The codebase uses `StrEnum`, which is 3.11+ |
| Bridge (`.venv-ros`) | 3.10 | Humble builds `rclpy` against `libpython3.10` |

They cannot be merged. Humble's `rclpy` will not import on 3.12, and the
platform will not import on 3.10 unless the bridge's import chain stays free of
3.11+ syntax and names — which is exactly what
`tests/test_x500v2_ros2_telemetry_adapter.py::RosInterpreterCompatibilityTests`
holds it to. If you add `datetime.UTC`, `StrEnum` or similar anywhere under
`brain/telemetry/` or `robots/drone/x500v2/ros2/`, that test fails and tells you
the bridge can no longer run.

Ubuntu 22.04 ships Python 3.10 as its system interpreter, so the platform venv
needs a newer one from the deadsnakes PPA.

## Host setup

```bash
# Python 3.12 for the platform venv
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get install -y python3.12 python3.12-venv python3.12-dev

# Build toolchain. zsh matters: the launchers use a zsh shebang.
sudo apt-get install -y zsh cmake ninja-build build-essential git

# Gazebo Harmonic
sudo curl -sSL https://packages.osrfoundation.org/gazebo.gpg \
  -o /usr/share/keyrings/pkgs-osrf-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/pkgs-osrf-archive-keyring.gpg] \
http://packages.osrfoundation.org/gazebo/ubuntu-stable $(lsb_release -cs) main" \
  | sudo tee /etc/apt/sources.list.d/gazebo-stable.list
sudo apt-get update && sudo apt-get install -y gz-harmonic

# ROS 2 Humble. ros-base is enough; the desktop metapackage adds GUI tools a
# headless server cannot use.
sudo curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
  -o /usr/share/keyrings/ros-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] \
http://packages.ros.org/ros2/ubuntu $(. /etc/os-release && echo $UBUNTU_CODENAME) main" \
  | sudo tee /etc/apt/sources.list.d/ros2.list
sudo apt-get update && sudo apt-get install -y ros-humble-ros-base \
  ros-humble-sensor-msgs ros-humble-std-msgs python3.10-venv
```

If `add-apt-repository` hangs on a Launchpad timeout, add the PPA by hand: read
`signing_key_fingerprint` from
`https://api.launchpad.net/devel/~deadsnakes/+archive/ubuntu/ppa`, fetch that key
from `keyserver.ubuntu.com`, and write a `signed-by=` source. Verify the
fingerprint against what the API reported rather than trusting the download.

## PX4

The checkout path must contain no spaces — a PX4 subproject breaks on them — and
`PX4-Autopilot` is a symlink to a sibling directory, git-ignored third-party
source.

```bash
git clone --recursive --branch v1.17.0 \
  https://github.com/PX4/PX4-Autopilot.git ~/bytewolf-robotics/PX4-Autopilot
ln -sfn ~/bytewolf-robotics/PX4-Autopilot ~/bytewolf-robotics/platform/PX4-Autopilot

cd ~/bytewolf-robotics/PX4-Autopilot
bash Tools/setup/ubuntu.sh --no-nuttx --no-sim-tools

# --no-sim-tools skips gazebo-classic, which would collide with Harmonic, but it
# also skips build dependencies the simulation modules need. Install those
# without gazebo-classic:
sudo apt-get install -y --no-install-recommends libopencv-dev libeigen3-dev \
  protobuf-compiler pkg-config libgstreamer-plugins-base1.0-dev libxml2-utils

export PATH="$HOME/.local/bin:$PATH"
make px4_sitl
```

**Build with `make px4_sitl`, not `make px4_sitl gz_x500`.** The second form also
*launches* the simulator, in interactive mode. On a headless host the `pxh>`
prompt then fills the output pipe without bound — it reached an 11 GB log file
here before anyone noticed. The launchers run PX4 in daemon mode (`-d`) for
exactly this reason.

## Platform environment

```bash
cd ~/bytewolf-robotics/platform
python3.12 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m unittest discover -s tests
```

The suite must be green here, with no PX4 and no Gazebo. If it is not, stop:
something is wrong that a simulator will not explain.

`requirements.txt` alone leaves the vision and biometric tests failing on missing
`cv2` and `cryptography`; those live in `requirements-vision-research.txt`, which
is deliberately outside the safety-core baseline. Install it too if you want the
whole suite, and note that CI installs only the former.

## Bridge environment

```bash
cd ~/bytewolf-robotics/platform
python3.10 -m venv --system-site-packages .venv-ros
.venv-ros/bin/pip install mavsdk jsonschema PyYAML certifi
```

`--system-site-packages` is what lets this venv see Humble's `rclpy` after the
ROS setup script is sourced.

## Baseline

```bash
.venv/bin/python -m simulation.baseline
```

It selects the `linux` profile on this host and prints which profile it chose.
The Linux profile expects an unpatched v1.17.0 tree — the recorded macOS build
patch must **not** be applied here — and Gazebo 8.14.0, the only release the OSRF
stable archive carries for jammy. Wind results from this host are close to the
macOS 8.12.0 numbers but not proven identical; see the profile's own comment.

## Running the bridge

Two terminals, in this order.

```bash
# 1 — simulator
cd ~/bytewolf-robotics/platform
./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base

# 2 — bridge, in the ROS interpreter
cd ~/bytewolf-robotics/platform
source /opt/ros/humble/setup.bash
PYTHONPATH="$PWD:$PYTHONPATH" .venv-ros/bin/python -m brain.cli.ros2_telemetry_bridge
```

Verify from a third shell, after sourcing ROS:

```bash
ros2 topic list | grep bytewolf
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/position
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/battery
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/flight_state
```

A topic existing is not evidence that anything was relayed. Echo them, and read
the run's own artifact under `simulation/artifacts/ros2-bridge/`: it records the
per-topic counts, which is what separates "the bridge relayed telemetry" from
"the bridge started". That directory is deliberately not the mission artifact
tree — a mission artifact says what was flown, the bridge artifact says what was
observed.

Exactly three topics are declared, and only those cross into ROS. The relay
carries more streams than that (attitude, IMU, GNSS detail, NED velocity, landed
state, battery diagnostics); they continue to the dashboard snapshot and are
withheld from ROS by contract. Widening that set is a versioned contract change
with its own safety review.

## The bridge holds the PX4 link

Unlike the dashboard relay, this bridge does not hand the link over. Start a
mission while it runs and the mission refuses itself:

```
Px4LinkUnavailableError: The PX4 endpoint is still held by another process.
Stop the telemetry bridge or the other mission, then try again; nothing was commanded.
```

That is fail-closed and correct — no command reached the vehicle — but it is not
the lease handover the dashboard path performs. Stop the bridge before flying a
mission.

## What this host cannot do

No physical vehicle is attached, and none is authorised from here. USB devices
must be passed through to reach a virtualised host at all. Camera capture needs a
real UVC device. None of that is required for this bridge, which is
telemetry-only and never commands anything.
