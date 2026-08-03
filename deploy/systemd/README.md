# systemd units for the simulation and Control Room stack

These run the ByteWolf stack on a dedicated Ubuntu host: PX4 SITL with Gazebo,
the dashboard telemetry relay, and the Control Room API. They exist so the stack
survives a reboot and a crash, which a hand-started `nohup` does not.

They assume the layout the Ubuntu runbook produces
(`docs/ros2-humble-bridge-ubuntu-runbook.md`): the checkout at
`~/bytewolf-robotics/platform`, the platform venv at `.venv`, and a built PX4 at
`../PX4-Autopilot/build/px4_sitl_default/bin/px4`.

## Install

```bash
sudo cp deploy/systemd/bytewolf-*.service deploy/systemd/bytewolf.target /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bytewolf.target
```

## Operate

```bash
systemctl status bytewolf.target          # the whole stack
systemctl status bytewolf-sitl.service    # one component
journalctl -u bytewolf-sitl -f            # follow its log

sudo systemctl stop bytewolf.target       # tears down in dependency order
sudo systemctl restart bytewolf-sitl      # the relay reconnects on its own
```

## What is deliberately not here

**The ROS 2 telemetry bridge.** It holds the PX4 link and does not hand it over,
so a mission started while it runs refuses itself with
`Px4LinkUnavailableError: … nothing was commanded`. That is fail-closed and
correct, but it makes the bridge a poor fit for an always-on unit. Start it by
hand when you want it, and stop it before flying:

```bash
source /opt/ros/humble/setup.bash
PYTHONPATH="$PWD:$PYTHONPATH" .venv-ros/bin/python -m brain.cli.ros2_telemetry_bridge
```

The dashboard relay *does* hand the link over — a mission announces itself, the
relay steps aside and reconnects — which is why that one can run continuously.

**Remote access.** The API binds to `127.0.0.1` in code and must keep doing so:
it is not purely read-only, and its mission endpoints reach PX4 through
MissionSpec validation, the SafetyGate and an explicit operator approval.
Exposure belongs to `tailscale serve`, which proxies from the tailnet to the
loopback socket without widening the bind:

```bash
sudo tailscale serve --bg 8080
```

Note what that means in practice: the Control Room becomes reachable by every
node on the tailnet, not only by you. Narrow it with ACLs if that is not what you
want.

**`simulation.gazebo.map_view`.** It injects a camera model into the running
world, so it must not run during any evidence run. Render with `--once` when you
need a background, never on an interval.

## Airframe profile

`bytewolf-sitl.service` starts the `base` profile (`gz_x500`), which carries no
camera and no lidar. For a camera or lidar airframe, change `ExecStart` to pass
another profile — `hawkeye-front`, `lidar-2d`, `full-sensors` — and reload. The
world map only fills for a lidar airframe; `base` can never produce a map cell.

## Evidence runs

Stop the stack before running a scenario matrix. The runners start and tear down
their own PX4 and Gazebo, and a second simulator on the same host competes for
the same MAVLink endpoint:

```bash
sudo systemctl stop bytewolf.target
.venv/bin/python -m simulation.scenarios.scenarios --matrix-version p0.v2
```
