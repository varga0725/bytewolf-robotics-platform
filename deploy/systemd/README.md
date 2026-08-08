# systemd units for the simulation and Control Room stack

These run the integrated ByteWolf stack on a dedicated Ubuntu host: PX4 SITL
with the `full-sensors` X500 (front camera, down camera and 2D lidar), dashboard
telemetry, two read-only camera relays, the occupancy-map observer, and the
Control Room API. They exist so the stack survives a reboot and a crash, which
a hand-started `nohup` does not.

They assume the layout the Ubuntu runbook produces
(`docs/ros2-humble-bridge-ubuntu-runbook.md`): the checkout at
`~/bytewolf-robotics/platform`, the platform venv at `.venv`, and a built PX4 at
`../PX4-Autopilot/build/px4_sitl_default/bin/px4`.

## Install or update

```bash
deploy/systemd/install-stack.sh
deploy/systemd/verify-stack.sh
```

The installer is idempotent. It validates the pinned PX4/Gazebo baseline and
the Python 3.10 Gazebo bindings, builds both frontends, verifies and installs
the units, and restarts the target. It deliberately does not install packages,
credentials, or publish the loopback API; the Ubuntu runbook owns host
prerequisites and remote access remains blocked.

## Operate

```bash
systemctl status bytewolf.target          # the whole stack
systemctl status bytewolf-sitl.service    # one component
journalctl -u bytewolf-camera-front -f    # follow one camera relay
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

**Remote access.** The API binds to `127.0.0.1` in code and must keep doing so.
It is not purely read-only: its mission endpoints can reach the simulation
executor after review and approval, while network authentication and RBAC are
not implemented. The installer therefore does not call `tailscale serve` or
publish the socket through any other tunnel. Remote publication remains
blocked until authentication, role separation, target allowlisting, rate and
replay protection, and an independently reviewed deployment policy are in
place. A tailnet alone is not an application authorization boundary.

If an older installation already configured Tailscale Serve, inspect that
external state and remove the specific ByteWolf forwarding rule through a
separate, human-reviewed maintenance action. This installer intentionally does
not issue a broad `tailscale serve reset`, because that could delete unrelated
services on the host.

**`simulation.gazebo.map_view`.** It injects a camera model into the running
world, so it must not run during any evidence run. Render with `--once` when you
need a background, never on an interval.

## Deployed airframe profile

`bytewolf-sitl.service` starts `full-sensors`. The front and down relays write
different atomic JPEG and detection artifacts, and `bytewolf-world-map` turns
only fresh 2D-lidar observations with a fresh vehicle pose into expiring
occupancy claims. The cheaper `base` profile remains useful for diagnosis, but
it is not the integrated deployment: it has neither camera nor lidar and can
never populate the camera panels or world map.

## Evidence runs

Stop the stack before running a scenario matrix. The runners start and tear down
their own PX4 and Gazebo, and a second simulator on the same host competes for
the same MAVLink endpoint:

```bash
sudo systemctl stop bytewolf.target
.venv/bin/python -m simulation.scenarios.scenarios --matrix-version p0.v2
```
