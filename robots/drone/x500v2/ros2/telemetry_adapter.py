"""Optional ROS 2 Humble adapter that can only publish declared telemetry.

The module remains import-safe on development hosts without ROS 2.  ROS types
are loaded only by :func:`create_ros2_telemetry_node` when a deployment opts in.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any, Protocol

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    TelemetryEvent,
    TelemetryContractError,
    route_mavsdk_telemetry,
)
from brain.telemetry.ros2_contract import load_ros2_telemetry_bridge_contract


class TelemetryAdapterError(ValueError):
    """Raised when the adapter would publish outside the declared contract."""


class Publisher(Protocol):
    """The narrow publishing capability the telemetry boundary needs."""

    def publish(self, message: object) -> None: ...


def declared_telemetry_topics() -> tuple[str, ...]:
    """Return the only ROS topics this adapter is allowed to publish."""
    return tuple(topic.name for topic in load_ros2_telemetry_bridge_contract().topics)


class Ros2TelemetryPublisher:
    """Publish immutable domain events through an exact declared-topic allowlist."""

    def __init__(
        self,
        publishers: Mapping[str, Publisher],
        *,
        encode: Callable[[TelemetryEvent], object] | None = None,
    ) -> None:
        expected_topics = frozenset(declared_telemetry_topics())
        if frozenset(publishers) != expected_topics:
            raise TelemetryAdapterError(
                "Telemetry adapter publishers must contain exactly the declared telemetry topics."
            )
        self._publishers = dict(publishers)
        self._encode = encode or _domain_payload

    def publish(self, event: TelemetryEvent) -> None:
        """Publish one valid event; no subscription or control path exists here."""
        publisher = self._publishers.get(event.topic)
        if publisher is None:
            raise TelemetryAdapterError(f"Telemetry event uses undeclared topic {event.topic!r}.")
        _validate_event_topic(event)
        publisher.publish(self._encode(event))


@dataclass(frozen=True)
class Ros2TelemetryNode:
    """The minimal optional ROS node surface: publish telemetry and close it."""

    node: Any
    telemetry: Ros2TelemetryPublisher

    def publish(self, event: TelemetryEvent) -> None:
        self.telemetry.publish(event)

    def destroy_node(self) -> None:
        self.node.destroy_node()


def create_ros2_telemetry_node(node_name: str = "bytewolf_x500v2_telemetry") -> Ros2TelemetryNode:
    """Create the opt-in ROS 2 Humble publisher node.

    This function deliberately creates publishers only.  It creates no
    subscriptions, services, actions, or MAVSDK/PX4 control capability.
    """
    try:
        from rclpy.node import Node
        from rclpy.qos import QoSProfile, ReliabilityPolicy, qos_profile_sensor_data
        from sensor_msgs.msg import BatteryState, NavSatFix
        from std_msgs.msg import String
    except ModuleNotFoundError as error:
        raise RuntimeError("ROS 2 Humble is required to create the telemetry adapter.") from error

    topics_by_source = {topic.source: topic for topic in load_ros2_telemetry_bridge_contract().topics}
    node = Node(node_name)
    publishers = {
        topics_by_source["MAVSDK telemetry.position"].name: node.create_publisher(
            NavSatFix,
            topics_by_source["MAVSDK telemetry.position"].name,
            _ros_qos(topics_by_source["MAVSDK telemetry.position"].qos_profile, qos_profile_sensor_data, QoSProfile, ReliabilityPolicy),
        ),
        topics_by_source["MAVSDK telemetry.battery"].name: node.create_publisher(
            BatteryState,
            topics_by_source["MAVSDK telemetry.battery"].name,
            _ros_qos(topics_by_source["MAVSDK telemetry.battery"].qos_profile, qos_profile_sensor_data, QoSProfile, ReliabilityPolicy),
        ),
        topics_by_source["MAVSDK telemetry.in_air"].name: node.create_publisher(
            String,
            topics_by_source["MAVSDK telemetry.in_air"].name,
            _ros_qos(topics_by_source["MAVSDK telemetry.in_air"].qos_profile, qos_profile_sensor_data, QoSProfile, ReliabilityPolicy),
        ),
    }
    return Ros2TelemetryNode(node, Ros2TelemetryPublisher(publishers, encode=_ros_message_encoder()))


def _ros_qos(
    profile_name: str, sensor_data: object, qos_profile_type: type[Any], reliability_policy: type[Any]
) -> object:
    if profile_name == "sensor_data":
        return sensor_data
    if profile_name == "reliable_status":
        return qos_profile_type(depth=10, reliability=reliability_policy.RELIABLE)
    raise TelemetryAdapterError(f"Unsupported declared QoS profile {profile_name!r}.")


def _validate_event_topic(event: TelemetryEvent) -> None:
    expected_types = {
        "MAVSDK telemetry.position": PositionTelemetryEvent,
        "MAVSDK telemetry.battery": BatteryTelemetryEvent,
        "MAVSDK telemetry.in_air": FlightStateTelemetryEvent,
    }
    topics_by_source = {topic.source: topic.name for topic in load_ros2_telemetry_bridge_contract().topics}
    for source, expected_type in expected_types.items():
        if event.topic == topics_by_source[source]:
            if isinstance(event, expected_type):
                try:
                    sample = event.in_air if isinstance(event, FlightStateTelemetryEvent) else event
                    routed_event = route_mavsdk_telemetry(
                        source, sample, observed_at=event.observed_at
                    )
                except TelemetryContractError as error:
                    raise TelemetryAdapterError(str(error)) from error
                if routed_event != event:
                    raise TelemetryAdapterError("Telemetry event does not match the declared contract.")
                return
            raise TelemetryAdapterError("Telemetry event type does not match its declared topic.")
    raise TelemetryAdapterError(f"Telemetry event uses undeclared topic {event.topic!r}.")


def _domain_payload(event: TelemetryEvent) -> dict[str, object]:
    if isinstance(event, PositionTelemetryEvent):
        return {
            "latitude_deg": event.latitude_deg,
            "longitude_deg": event.longitude_deg,
            "absolute_altitude_m": event.absolute_altitude_m,
            "relative_altitude_m": event.relative_altitude_m,
            "observed_at": event.observed_at,
        }
    if isinstance(event, BatteryTelemetryEvent):
        return {"remaining_percent": event.remaining_percent, "observed_at": event.observed_at}
    if isinstance(event, FlightStateTelemetryEvent):
        return {"in_air": event.in_air, "observed_at": event.observed_at}
    raise TelemetryAdapterError("Unsupported telemetry event.")


def _ros_message_encoder() -> Callable[[TelemetryEvent], object]:
    from sensor_msgs.msg import BatteryState
    from sensor_msgs.msg import NavSatFix
    from std_msgs.msg import String

    def encode(event: TelemetryEvent) -> object:
        if isinstance(event, PositionTelemetryEvent):
            message = NavSatFix()
            _set_stamp(message, event.observed_at.timestamp())
            message.latitude = event.latitude_deg
            message.longitude = event.longitude_deg
            message.altitude = event.absolute_altitude_m
            return message
        if isinstance(event, BatteryTelemetryEvent):
            message = BatteryState()
            _set_stamp(message, event.observed_at.timestamp())
            message.percentage = event.remaining_percent
            return message
        if isinstance(event, FlightStateTelemetryEvent):
            message = String()
            message.data = json.dumps(
                {"in_air": event.in_air, "observed_at": event.observed_at.isoformat()},
                separators=(",", ":"),
            )
            return message
        raise TelemetryAdapterError("Unsupported telemetry event.")

    return encode


def _set_stamp(message: object, timestamp: float) -> None:
    seconds = int(timestamp)
    nanoseconds = int((timestamp - seconds) * 1_000_000_000)
    message.header.stamp.sec = seconds
    message.header.stamp.nanosec = nanoseconds
