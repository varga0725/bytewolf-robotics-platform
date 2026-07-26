"""Contract tests for the ROS-independent ROS 2 telemetry bridge scaffold."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import jsonschema
import yaml

from brain.telemetry.ros2_contract import (
    DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG,
    load_ros2_telemetry_bridge_contract,
)


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "shared/schemas/ros2_telemetry/telemetry_bridge_v0_2.schema.json"


class Ros2TelemetryContractTests(unittest.TestCase):
    def test_versioned_configuration_conforms_to_the_public_contract(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        configuration = yaml.safe_load(
            DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8")
        )

        jsonschema.validate(configuration, schema)

    def test_loader_exposes_the_contract_without_requiring_ros(self) -> None:
        contract = load_ros2_telemetry_bridge_contract()

        self.assertEqual(contract.version, "v0.2")
        self.assertEqual(contract.vehicle_id, "x500v2_reference_01")
        self.assertEqual(contract.namespace, "/bytewolf/x500v2_reference_01")
        self.assertEqual(
            tuple(topic.name for topic in contract.topics),
            (
                "/bytewolf/x500v2_reference_01/telemetry/position",
                "/bytewolf/x500v2_reference_01/telemetry/battery",
                "/bytewolf/x500v2_reference_01/telemetry/flight_state",
            ),
        )
        self.assertEqual(
            tuple(topic.message_type for topic in contract.topics),
            (
                "sensor_msgs/msg/NavSatFix",
                "sensor_msgs/msg/BatteryState",
                "std_msgs/msg/String",
            ),
        )

    def test_loader_rejects_a_topic_outside_the_declared_namespace(self) -> None:
        document = yaml.safe_load(DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8"))
        document["topics"][0]["name"] = "/bytewolf/another_vehicle/telemetry/position"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "namespace"):
                load_ros2_telemetry_bridge_contract(path)

    def test_loader_rejects_a_semantically_unsafe_position_message_type(self) -> None:
        document = yaml.safe_load(DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8"))
        document["topics"][0]["message_type"] = "geometry_msgs/msg/PoseStamped"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "message type"):
                load_ros2_telemetry_bridge_contract(path)

    def test_loader_rejects_a_namespace_that_does_not_match_the_vehicle_id(self) -> None:
        document = yaml.safe_load(DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8"))
        document["namespace"] = "/bytewolf/another_vehicle"
        for topic in document["topics"]:
            topic["name"] = topic["name"].replace("x500v2_reference_01", "another_vehicle")

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "vehicle_id"):
                load_ros2_telemetry_bridge_contract(path)


if __name__ == "__main__":
    unittest.main()
