"""Read and validate the declarative ROS 2 telemetry bridge contract.

This module deliberately contains no ROS imports.  It makes the future bridge
configuration reviewable and testable on the current macOS-only V1 environment.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROS2_TELEMETRY_BRIDGE_VERSION = "v0.2"
DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG = (
    Path(__file__).resolve().parents[2]
    / "shared/config/x500v2/ros2_telemetry_bridge.v0_2.yaml"
)

_DECLARED_TOPICS = frozenset(
    {
        (
            "telemetry/position",
            "sensor_msgs/msg/NavSatFix",
            "MAVSDK telemetry.position",
            "sensor_data",
        ),
        (
            "telemetry/battery",
            "sensor_msgs/msg/BatteryState",
            "MAVSDK telemetry.battery",
            "reliable_status",
        ),
        (
            "telemetry/flight_state",
            "std_msgs/msg/String",
            "MAVSDK telemetry.in_air",
            "reliable_status",
        ),
    }
)


@dataclass(frozen=True)
class Ros2TelemetryTopic:
    """One ROS 2 topic declared by the bridge boundary."""

    name: str
    message_type: str
    source: str
    qos_profile: str


@dataclass(frozen=True)
class Ros2TelemetryBridgeContract:
    """Immutable, ROS-independent representation of the bridge configuration."""

    version: str
    namespace: str
    vehicle_id: str
    topics: tuple[Ros2TelemetryTopic, ...]


def load_ros2_telemetry_bridge_contract(
    configuration_path: Path = DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG,
) -> Ros2TelemetryBridgeContract:
    """Load the versioned contract and reject unsafe or ambiguous declarations."""
    document = yaml.safe_load(configuration_path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("ROS 2 telemetry bridge configuration must be a mapping.")

    version = _required_string(document, "version")
    if version != ROS2_TELEMETRY_BRIDGE_VERSION:
        raise ValueError(f"Unsupported ROS 2 telemetry bridge version: {version!r}.")

    namespace = _required_string(document, "namespace")
    vehicle_id = _required_string(document, "vehicle_id")
    if namespace != f"/bytewolf/{vehicle_id}":
        raise ValueError("ROS 2 telemetry bridge namespace must match vehicle_id.")
    topic_documents = document.get("topics")
    if not isinstance(topic_documents, list) or not topic_documents:
        raise ValueError("ROS 2 telemetry bridge configuration must declare topics.")

    topics = tuple(_parse_topic(topic_document) for topic_document in topic_documents)
    topic_names = tuple(topic.name for topic in topics)
    if len(topic_names) != len(set(topic_names)):
        raise ValueError("ROS 2 telemetry bridge topic names must be unique.")
    if any(not topic.name.startswith(f"{namespace}/") for topic in topics):
        raise ValueError("ROS 2 telemetry bridge topics must remain inside their namespace.")
    _validate_declared_topics(namespace, topics)

    return Ros2TelemetryBridgeContract(version, namespace, vehicle_id, topics)


def _validate_declared_topics(namespace: str, topics: tuple[Ros2TelemetryTopic, ...]) -> None:
    actual_topics = frozenset(
        (
            topic.name.removeprefix(f"{namespace}/"),
            topic.message_type,
            topic.source,
            topic.qos_profile,
        )
        for topic in topics
    )
    if actual_topics != _DECLARED_TOPICS:
        raise ValueError(
            "ROS 2 telemetry bridge topics, sources, message types, and QoS must match the v0.2 contract."
        )


def _parse_topic(document: Any) -> Ros2TelemetryTopic:
    if not isinstance(document, dict):
        raise ValueError("Each ROS 2 telemetry topic must be a mapping.")
    return Ros2TelemetryTopic(
        name=_required_string(document, "name"),
        message_type=_required_string(document, "message_type"),
        source=_required_string(document, "source"),
        qos_profile=_required_string(document, "qos_profile"),
    )


def _required_string(document: dict[str, Any], key: str) -> str:
    value = document.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ROS 2 telemetry bridge field {key!r} must be a non-empty string.")
    return value
