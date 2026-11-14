"""Tests for the optional, telemetry-only X500 V2 ROS 2 adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import importlib
import math
import shutil
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
)
from robots.drone.x500v2.ros2.telemetry_adapter import (
    Ros2TelemetryPublisher,
    TelemetryAdapterError,
    declared_telemetry_topics,
)


POSITION_TOPIC = "/bytewolf/x500v2_reference_01/telemetry/position"
BATTERY_TOPIC = "/bytewolf/x500v2_reference_01/telemetry/battery"
FLIGHT_STATE_TOPIC = "/bytewolf/x500v2_reference_01/telemetry/flight_state"
OBSERVED_AT = datetime(2026, 7, 16, 18, 0, tzinfo=UTC)


class FakePublisher:
    def __init__(self) -> None:
        self.messages: list[object] = []

    def publish(self, message: object) -> None:
        self.messages.append(message)


class X500V2Ros2TelemetryAdapterTests(unittest.TestCase):
    def test_is_import_safe_without_a_ros_installation(self) -> None:
        module = importlib.import_module("robots.drone.x500v2.ros2.telemetry_adapter")

        self.assertFalse(hasattr(module, "rclpy"))
        self.assertEqual(
            declared_telemetry_topics(),
            (POSITION_TOPIC, BATTERY_TOPIC, FLIGHT_STATE_TOPIC),
        )

    def test_publishes_only_the_three_declared_telemetry_topics(self) -> None:
        publishers = {topic: FakePublisher() for topic in declared_telemetry_topics()}
        adapter = Ros2TelemetryPublisher(publishers)

        adapter.publish(
            PositionTelemetryEvent(POSITION_TOPIC, 47.4979, 19.0402, 125.5, 15.0, OBSERVED_AT)
        )
        adapter.publish(BatteryTelemetryEvent(BATTERY_TOPIC, 0.75, OBSERVED_AT))
        adapter.publish(FlightStateTelemetryEvent(FLIGHT_STATE_TOPIC, True, OBSERVED_AT))

        self.assertEqual(
            publishers[POSITION_TOPIC].messages,
            [{"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5, "relative_altitude_m": 15.0, "observed_at": OBSERVED_AT}],
        )
        self.assertEqual(publishers[BATTERY_TOPIC].messages, [{"remaining_percent": 0.75, "observed_at": OBSERVED_AT}])
        self.assertEqual(publishers[FLIGHT_STATE_TOPIC].messages, [{"in_air": True, "observed_at": OBSERVED_AT}])

    def test_rejects_publishers_or_events_outside_the_declared_contract(self) -> None:
        with self.assertRaisesRegex(TelemetryAdapterError, "exactly"):
            Ros2TelemetryPublisher({POSITION_TOPIC: FakePublisher()})

        adapter = Ros2TelemetryPublisher(
            {topic: FakePublisher() for topic in declared_telemetry_topics()}
        )
        with self.assertRaisesRegex(TelemetryAdapterError, "undeclared"):
            adapter.publish(BatteryTelemetryEvent("/bytewolf/x500v2/control/arm", 0.75, OBSERVED_AT))

        with self.assertRaisesRegex(TelemetryAdapterError, "finite"):
            adapter.publish(
                PositionTelemetryEvent(
                    POSITION_TOPIC, math.nan, 19.0402, 125.5, 15.0, OBSERVED_AT
                )
            )


class RosInterpreterCompatibilityTests(unittest.TestCase):
    """The bridge must stay importable on the Python its ROS distribution ships.

    ROS 2 Humble builds ``rclpy`` against Python 3.10, while the rest of this
    platform requires 3.11 or newer. The bridge therefore runs in a separate
    interpreter, and the only thing that makes that possible is the import chain
    below staying free of 3.11+ syntax and library names. Nothing about that
    constraint is visible while editing ``brain/telemetry``, so this test is what
    stops a future change from breaking the bridge silently.
    """

    CHAIN = (
        "brain.telemetry.domain",
        "brain.telemetry.observation",
        "brain.telemetry.persistence",
        "brain.telemetry.mavsdk_relay",
        "brain.telemetry.ulog",
        "brain.telemetry.ros2_contract",
        "robots.drone.x500v2.ros2.telemetry_adapter",
        "robots.drone.x500v2.ros2.bridge_runtime",
    )
    # The interpreters ROS 2 distributions pin. Humble is the one this project
    # declares; the others let the check keep working if that ever moves.
    CANDIDATES = ("python3.10", "python3.11", "python3.12")

    def _interpreter(self) -> str:
        """Return a ROS-era interpreter that can already satisfy the chain's third-party deps."""
        for name in self.CANDIDATES:
            executable = shutil.which(name)
            if executable is None:
                continue
            probe = subprocess.run(
                (executable, "-c", "import yaml, jsonschema"),
                capture_output=True,
                text=True,
                timeout=60.0,
                check=False,
            )
            if probe.returncode == 0:
                return executable
        self.skipTest("No interpreter with PyYAML and jsonschema available to check the ROS chain.")

    def test_the_bridge_chain_imports_on_the_ros_interpreter(self) -> None:
        executable = self._interpreter()
        program = "import sys; sys.path.insert(0, %r)\n" % str(ROOT) + "".join(
            f"import {module}\n" for module in self.CHAIN
        )

        completed = subprocess.run(
            (executable, "-c", program), capture_output=True, text=True, timeout=120.0, check=False
        )

        self.assertEqual(
            completed.returncode,
            0,
            f"{executable} cannot import the ROS bridge chain, so the bridge cannot run beside "
            f"rclpy:\n{completed.stderr.strip()}",
        )

    def test_importing_the_chain_does_not_pull_in_rclpy(self) -> None:
        """A bridge that imported rclpy eagerly would stop being optional."""
        executable = self._interpreter()
        program = "import sys; sys.path.insert(0, %r)\n" % str(ROOT) + "".join(
            f"import {module}\n" for module in self.CHAIN
        ) + "raise SystemExit(1 if 'rclpy' in sys.modules else 0)\n"

        completed = subprocess.run(
            (executable, "-c", program), capture_output=True, text=True, timeout=120.0, check=False
        )

        self.assertEqual(completed.returncode, 0, "Importing the bridge chain must not import rclpy.")


if __name__ == "__main__":
    unittest.main()
