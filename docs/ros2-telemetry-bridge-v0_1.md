# ROS 2 Humble telemetry bridge — v0.2

## V1 release boundary

This is a telemetry-only P1 boundary.  The optional adapter can be imported on
macOS without ROS; creating its ROS node requires ROS 2 Humble in Linux. It
does not change mission execution. Existing PX4/MAVSDK mission commands remain
the only active flight-control path.

The contract is intentionally readable and validated by the normal Python test
suite. The live bridge was exercised on the dedicated Ubuntu 22.04 / ROS 2
Humble host on 2026-08-03; macOS remains suitable for ROS-independent contract
review but does not provide `rclpy`.

## Versioned inputs

- Public schema: `shared/schemas/ros2_telemetry/telemetry_bridge_v0_2.schema.json`
- X500 V2 contract: `shared/config/x500v2/ros2_telemetry_bridge.v0_2.yaml`
- ROS-independent reader: `brain/telemetry/ros2_contract.py`
- Optional ROS node: `robots/drone/x500v2/ros2/telemetry_adapter.py`

The contract names three telemetry-only topics under the vehicle namespace:

| Topic suffix | ROS message type | Source | QoS profile |
| --- | --- | --- | --- |
| `telemetry/position` | `sensor_msgs/msg/NavSatFix` | MAVSDK position | `sensor_data` |
| `telemetry/battery` | `sensor_msgs/msg/BatteryState` | MAVSDK battery | `reliable_status` |
| `telemetry/flight_state` | `std_msgs/msg/String` | MAVSDK in-air state | `reliable_status` |

The bridge is telemetry-only. It must not accept control, arming, mode-change,
or mission-command topics. Such a control path needs its own safety review,
versioned contract, and release decision.

The v0.2 position message is global WGS84 latitude/longitude/altitude. This
replaces the unsound v0.1 `PoseStamped` proposal: no local map frame or origin
is inferred. The old v0.1 files remain only as historical preparation material
and are not loaded by the bridge.

## Local visual dashboard

`apps/dashboard` is a separate, local-only (`127.0.0.1`) and read-only view.
It reads a JSON snapshot, shows position, battery and in-air state, and labels
the data as `LIVE`, `STALE`, `FUTURE`, `MISSING` or `INVALID`. It exposes only
`GET /` and `GET /api/telemetry`; any `POST` is rejected. It is intentionally
not a flight-control user interface.

For the exact native macOS visual replay/SITL procedure and the separately
scoped Ubuntu Humble smoke procedure, see
[`visual-simulation-verification.md`](visual-simulation-verification.md).
The replay dashboard is not a live flight display. The separate, optional
`brain.cli.ros2_telemetry_bridge` process owns the live MAVSDK → ROS → JSON
telemetry lifecycle in Ubuntu Humble. Its default is an explicit `simulation`
deployment and the loopback endpoint `udpin://127.0.0.1:14540`; it has no
actuation surface.

## Verified Ubuntu Humble state

The preserved SITL-only run artifact
`simulation/artifacts/ros2-bridge/ros2-bridge-20260803T154829Z-5a78027bee68435c9d884b241197155f.json`
records a completed bridge lifecycle with 1,351 position, 17 battery, and 136
flight-state publications. Ten richer history streams were explicitly withheld
from the public ROS contract. This proves the Ubuntu SITL telemetry path only;
it is not physical-vehicle or flight-control evidence.

The exact host setup and repeatable checks are in
[`ros2-humble-bridge-ubuntu-runbook.md`](ros2-humble-bridge-ubuntu-runbook.md).
Keep the bridge optional and fail closed: if it is unavailable, telemetry
publication may be absent, but it must never block or alter mission safety.

## Contract changes

Any incompatible topic rename, message-type change, source change, or QoS
change requires a new contract version and migration documentation. Additive
topics may be introduced only after schema, loader, and contract tests are
updated together.
