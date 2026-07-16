"""Tests for the ROS-independent, contract-driven telemetry domain."""

from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    TelemetryContractError,
    route_mavsdk_telemetry,
)


class PositionSample:
    latitude_deg = 47.4979
    longitude_deg = 19.0402
    absolute_altitude_m = 125.5
    relative_altitude_m = 15.0


class BatterySample:
    remaining_percent = 0.75


class TelemetryDomainTests(unittest.TestCase):
    observed_at = datetime(2026, 7, 16, 18, 30, tzinfo=UTC)

    def test_routes_supported_mavsdk_samples_to_declared_contract_topics(self) -> None:
        position = route_mavsdk_telemetry(
            "MAVSDK telemetry.position", PositionSample(), observed_at=self.observed_at
        )
        battery = route_mavsdk_telemetry(
            "MAVSDK telemetry.battery", BatterySample(), observed_at=self.observed_at
        )
        flight_state = route_mavsdk_telemetry(
            "MAVSDK telemetry.in_air", True, observed_at=self.observed_at
        )

        self.assertEqual(
            position,
            PositionTelemetryEvent(
                topic="/bytewolf/x500v2_reference_01/telemetry/position",
                latitude_deg=47.4979,
                longitude_deg=19.0402,
                absolute_altitude_m=125.5,
                relative_altitude_m=15.0,
                observed_at=self.observed_at,
            ),
        )
        self.assertEqual(
            battery,
            BatteryTelemetryEvent(
                topic="/bytewolf/x500v2_reference_01/telemetry/battery",
                remaining_percent=0.75,
                observed_at=self.observed_at,
            ),
        )
        self.assertEqual(
            flight_state,
            FlightStateTelemetryEvent(
                topic="/bytewolf/x500v2_reference_01/telemetry/flight_state",
                in_air=True,
                observed_at=self.observed_at,
            ),
        )

    def test_rejects_unknown_or_undeclared_mavsdk_sources(self) -> None:
        with self.assertRaisesRegex(TelemetryContractError, "unknown"):
            route_mavsdk_telemetry("MAVSDK telemetry.health", object())

    def test_rejects_malformed_samples(self) -> None:
        with self.assertRaisesRegex(TelemetryContractError, "latitude_deg"):
            route_mavsdk_telemetry("MAVSDK telemetry.position", object())
        with self.assertRaisesRegex(TelemetryContractError, "boolean"):
            route_mavsdk_telemetry("MAVSDK telemetry.in_air", 1)

    def test_rejects_non_finite_and_out_of_range_values(self) -> None:
        bad_position = PositionSample()
        bad_position.longitude_deg = math.nan
        with self.assertRaisesRegex(TelemetryContractError, "finite"):
            route_mavsdk_telemetry("MAVSDK telemetry.position", bad_position)

        bad_battery = BatterySample()
        bad_battery.remaining_percent = 1.1
        with self.assertRaisesRegex(TelemetryContractError, "between 0.0 and 1.0"):
            route_mavsdk_telemetry("MAVSDK telemetry.battery", bad_battery)


if __name__ == "__main__":
    unittest.main()
