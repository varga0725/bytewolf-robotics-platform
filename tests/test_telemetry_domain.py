"""Tests for the ROS-independent, contract-driven telemetry domain."""

from __future__ import annotations

import math
import unittest
from datetime import UTC, datetime

from mavsdk.telemetry import BatteryFunction

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
    remaining_percent = 75.0


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
                remaining_percent=75.0,
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
            route_mavsdk_telemetry("MAVSDK telemetry.not_a_stream", object())

    def test_routes_validated_extended_vehicle_state_for_durable_history(self) -> None:
        velocity = route_mavsdk_telemetry(
            "MAVSDK telemetry.velocity_ned",
            type("Velocity", (), {"north_m_s": 1.0, "east_m_s": 2.0, "down_m_s": -0.5})(),
            observed_at=self.observed_at,
        )
        attitude = route_mavsdk_telemetry(
            "MAVSDK telemetry.attitude_euler",
            type("Attitude", (), {"roll_deg": 1.0, "pitch_deg": -2.0, "yaw_deg": 90.0})(),
            observed_at=self.observed_at,
        )
        self.assertEqual(dict(velocity.payload)["north_m_s"], 1.0)
        self.assertEqual(dict(attitude.payload)["yaw_deg"], 90.0)

    def test_routes_a_complete_imu_sample_in_frd_coordinates(self) -> None:
        vector = type("Vector", (), {"forward_m_s2": 1.0, "right_m_s2": 2.0, "down_m_s2": 3.0})()
        angular = type("Angular", (), {"forward_rad_s": 0.1, "right_rad_s": 0.2, "down_rad_s": 0.3})()
        magnetic = type("Magnetic", (), {"forward_gauss": 0.01, "right_gauss": 0.02, "down_gauss": 0.03})()
        sample = type("Imu", (), {"acceleration_frd": vector, "angular_velocity_frd": angular, "magnetic_field_frd": magnetic, "temperature_degc": 25.0})()
        event = route_mavsdk_telemetry("MAVSDK telemetry.imu", sample, observed_at=self.observed_at)
        self.assertEqual(dict(event.payload)["forward_m_s2"], 1.0)
        self.assertEqual(dict(event.payload)["down_gauss"], 0.03)

    def test_routes_sitl_ground_truth_as_validated_history_evidence(self) -> None:
        sample = type(
            "GroundTruth",
            (),
            {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5},
        )()

        event = route_mavsdk_telemetry(
            "MAVSDK telemetry.ground_truth", sample, observed_at=self.observed_at
        )

        self.assertEqual(event.topic, "telemetry/history/ground_truth")
        self.assertEqual(
            dict(event.payload),
            {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5},
        )

    def test_routes_local_position_velocity_in_ned_coordinates(self) -> None:
        position = type("PositionNed", (), {"north_m": 2.0, "east_m": -1.0, "down_m": -3.0})()
        velocity = type(
            "VelocityNed", (), {"north_m_s": 0.5, "east_m_s": -0.25, "down_m_s": 0.1}
        )()
        sample = type("PositionVelocityNed", (), {"position": position, "velocity": velocity})()

        event = route_mavsdk_telemetry(
            "MAVSDK telemetry.position_velocity_ned", sample, observed_at=self.observed_at
        )

        self.assertEqual(dict(event.payload)["down_m"], -3.0)
        self.assertEqual(dict(event.payload)["east_m_s"], -0.25)

    def test_keeps_available_battery_diagnostics_but_never_invents_nan_values(self) -> None:
        sample = type(
            "Battery",
            (),
            {"id": 0, "voltage_v": 15.2, "current_battery_a": 3.4, "capacity_consumed_ah": float("nan"), "time_remaining_s": 120.0, "temperature_degc": float("nan"), "battery_function": BatteryFunction.ALL},
        )()
        event = route_mavsdk_telemetry(
            "MAVSDK telemetry.battery_diagnostics", sample, observed_at=self.observed_at
        )
        payload = dict(event.payload)
        self.assertEqual(payload["voltage_v"], 15.2)
        self.assertEqual(payload["battery_function"], "all")
        self.assertNotIn("temperature_degc", payload)
        self.assertNotIn("capacity_consumed_ah", payload)

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
        bad_battery.remaining_percent = 100.1
        with self.assertRaisesRegex(TelemetryContractError, "between 0.0 and 100.0"):
            route_mavsdk_telemetry("MAVSDK telemetry.battery", bad_battery)


class EnumSourceTests(unittest.TestCase):
    """Sources that publish one bare enum, read as MAVSDK actually sends them.

    `flight_mode` and `landed_state` died on their first sample of every real
    run and appear in no recorded telemetry history. The contract names their
    single field `"value"`, and reading it with `getattr(sample, "value")`
    looked equivalent — but MAVSDK's FlightMode and LandedState are Python
    Enums, and an Enum *has* a `.value`: the underlying integer. Both streams
    resolved to an int and failed the string check.

    Nothing caught it because every fake in this suite passes a plain value.
    These use the real enums for exactly that reason.
    """

    def test_a_flight_mode_enum_yields_its_name_not_its_integer(self) -> None:
        from mavsdk.telemetry import FlightMode

        event = route_mavsdk_telemetry("MAVSDK telemetry.flight_mode", FlightMode.HOLD)

        self.assertEqual(event.payload, (("value", "hold"),))

    def test_a_landed_state_enum_yields_its_name_not_its_integer(self) -> None:
        from mavsdk.telemetry import LandedState

        event = route_mavsdk_telemetry("MAVSDK telemetry.landed_state", LandedState.IN_AIR)

        self.assertEqual(event.payload, (("value", "in_air"),))

    def test_the_bare_boolean_source_still_reads_as_a_boolean(self) -> None:
        event = route_mavsdk_telemetry("MAVSDK telemetry.armed", True)

        self.assertEqual(event.payload, (("value", True),))

    def test_an_enum_nested_in_a_sample_is_unaffected(self) -> None:
        """`gps_info.fix_type` reads a real attribute and always worked."""
        from mavsdk.telemetry import FixType, GpsInfo

        event = route_mavsdk_telemetry(
            "MAVSDK telemetry.gps_info", GpsInfo(num_satellites=12, fix_type=FixType.FIX_3D)
        )

        self.assertEqual(event.payload, (("num_satellites", 12), ("fix_type", "fix_3d")))

    def test_a_bare_integer_is_still_refused_where_a_name_is_required(self) -> None:
        with self.assertRaisesRegex(TelemetryContractError, "non-empty string"):
            route_mavsdk_telemetry("MAVSDK telemetry.flight_mode", 3)


if __name__ == "__main__":
    unittest.main()
