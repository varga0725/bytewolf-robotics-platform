# ROS 2 Humble telemetry bridge — v0.1 scaffold

## V1 release boundary

This is a **preparation contract**, not a ROS runtime feature. The V1 release
does not require ROS 2 Humble, `rclpy`, a Linux VM, a bridge process, or any
change to mission execution. Existing PX4/MAVSDK mission commands remain the
only active flight-control path.

The contract is intentionally readable and validated by the normal Python test
suite, so it can be reviewed on the native macOS simulator environment before a
Linux ROS 2 deployment is introduced.

## Versioned inputs

- Public schema: `interfaces/ros2_telemetry/telemetry_bridge_v0_1.schema.json`
- X500 V2 contract: `platforms/x500v2/config/ros2_telemetry_bridge.v0_1.yaml`
- ROS-independent reader: `brain/telemetry/ros2_contract.py`

The contract names three telemetry-only topics under the vehicle namespace:

| Topic suffix | ROS message type | Source | QoS profile |
| --- | --- | --- | --- |
| `telemetry/position` | `geometry_msgs/msg/PoseStamped` | MAVSDK position | `sensor_data` |
| `telemetry/battery` | `sensor_msgs/msg/BatteryState` | MAVSDK battery | `reliable_status` |
| `telemetry/flight_state` | `std_msgs/msg/String` | MAVSDK in-air state | `reliable_status` |

The bridge is telemetry-only. It must not accept control, arming, mode-change,
or mission-command topics. Such a control path needs its own safety review,
versioned contract, and release decision.

## Future Humble implementation runbook

1. Provision ROS 2 Humble in the optional Linux VM; do not add ROS as a V1
   Python dependency.
2. Implement a separate bridge package which reads the v0.1 configuration and
   publishes only the declared topic names, message types, and QoS profiles.
3. Convert MAVSDK values with explicit timestamps and frame conventions. Do not
   infer a coordinate frame: document it in a new contract version if needed.
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
