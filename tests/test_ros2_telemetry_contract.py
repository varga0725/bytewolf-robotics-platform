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
SCHEMA_PATH = ROOT / "interfaces/ros2_telemetry/telemetry_bridge_v0_1.schema.json"


class Ros2TelemetryContractTests(unittest.TestCase):
    def test_versioned_configuration_conforms_to_the_public_contract(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        configuration = yaml.safe_load(
            DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8")
        )

        jsonschema.validate(configuration, schema)

    def test_loader_exposes_the_contract_without_requiring_ros(self) -> None:
        contract = load_ros2_telemetry_bridge_contract()

        self.assertEqual(contract.version, "v0.1")
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

    def test_loader_rejects_a_topic_outside_the_declared_namespace(self) -> None:
        document = yaml.safe_load(DEFAULT_ROS2_TELEMETRY_BRIDGE_CONFIG.read_text(encoding="utf-8"))
        document["topics"][0]["name"] = "/bytewolf/another_vehicle/telemetry/position"

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "contract.yaml"
            path.write_text(yaml.safe_dump(document), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "namespace"):
                load_ros2_telemetry_bridge_contract(path)


if __name__ == "__main__":
    unittest.main()
