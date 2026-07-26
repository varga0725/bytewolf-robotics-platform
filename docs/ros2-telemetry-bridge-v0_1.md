# ROS 2 Humble telemetry bridge — v0.2

## V1 release boundary

This is a telemetry-only P1 boundary.  The optional adapter can be imported on
macOS without ROS; creating its ROS node requires ROS 2 Humble in Linux. It
does not change mission execution. Existing PX4/MAVSDK mission commands remain
the only active flight-control path.

The contract is intentionally readable and validated by the normal Python test
suite, so it can be reviewed on the native macOS simulator environment before a
Linux ROS 2 deployment is introduced.

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
The replay dashboard is not a live flight display.  The separate, optional
`brain.cli.ros2_telemetry_bridge` process owns the live MAVSDK → ROS → JSON
telemetry lifecycle in Ubuntu Humble; it must be verified there before it is
represented as a live display.

## Future Humble implementation runbook

1. Provision ROS 2 Humble in the optional Linux VM; do not add ROS as a V1
   Python dependency.
2. Run the separate bridge process which reads the v0.2 configuration and
   publishes only the declared topic names, message types, and QoS profiles.
3. Convert MAVSDK values with explicit timestamps. Global position is emitted
   as `NavSatFix`; do not transform it to a local frame without a new contract.
4. Run the contract tests before integration, then test with PX4 SITL/Gazebo in
   the VM. Capture bridge logs separately from mission audit artifacts.
5. Keep the bridge process optional and fail closed: if it is unavailable,
   telemetry publication may be absent, but it must never block or alter the
   existing MAVSDK mission safety path.

## Contract changes

Any incompatible topic rename, message-type change, source change, or QoS
change requires a new contract version and migration documentation. Additive
topics may be introduced only after schema, loader, and contract tests are
updated together.
