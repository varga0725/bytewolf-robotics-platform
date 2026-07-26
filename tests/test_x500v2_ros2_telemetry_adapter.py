"""Tests for the optional, telemetry-only X500 V2 ROS 2 adapter."""

from __future__ import annotations

from datetime import UTC, datetime
import importlib
import math
import unittest

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


if __name__ == "__main__":
    unittest.main()
