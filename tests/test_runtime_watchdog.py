"""Contract tests for app-side in-flight telemetry safety monitoring."""

from dataclasses import FrozenInstanceError
import unittest

from brain.mission.runtime_watchdog import (
    RuntimeFault,
    RuntimeFaultKind,
    RuntimeSafetyAction,
    RuntimeTelemetryWatchdog,
)


class Battery:
    def __init__(self, remaining_percent: float) -> None:
        self.remaining_percent = remaining_percent


class Position:
    def __init__(self, latitude_deg: float = 47.5) -> None:
        self.latitude_deg = latitude_deg
        self.longitude_deg = 19.1
        self.absolute_altitude_m = 120.0


class RuntimeTelemetryWatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.watchdog = RuntimeTelemetryWatchdog(
            minimum_battery_percent=35.0,
            telemetry_sample_timeout_s=2.0,
        )

    def test_allows_valid_runtime_samples_above_the_reserve(self) -> None:
        decision = self.watchdog.evaluate(Battery(0.36), Position())

        self.assertTrue(decision.permitted)
        self.assertIsNone(decision.fault)

    def test_orders_one_land_fallback_for_low_battery_while_client_is_alive(self) -> None:
        decision = self.watchdog.evaluate(Battery(0.34), Position())

        self.assertFalse(decision.permitted)
        self.assertEqual(decision.action, RuntimeSafetyAction.LAND)
        self.assertEqual(decision.fault.kind, RuntimeFaultKind.LOW_BATTERY)
        with self.assertRaises(FrozenInstanceError):
            decision.fault.kind = RuntimeFaultKind.GNSS_INVALID  # type: ignore[misc]

    def test_orders_one_land_fallback_for_invalid_gnss(self) -> None:
        decision = self.watchdog.evaluate(Battery(0.8), Position(float("nan")))

        self.assertFalse(decision.permitted)
        self.assertEqual(decision.action, RuntimeSafetyAction.LAND)
        self.assertEqual(decision.fault.kind, RuntimeFaultKind.GNSS_INVALID)

    def test_orders_one_land_fallback_when_a_telemetry_stream_stops_or_times_out(self) -> None:
        decision = self.watchdog.telemetry_unavailable("battery")

        self.assertFalse(decision.permitted)
        self.assertEqual(decision.action, RuntimeSafetyAction.LAND)
        self.assertEqual(decision.fault, RuntimeFault(RuntimeFaultKind.TELEMETRY_UNAVAILABLE, "battery"))

    def test_does_not_claim_an_app_side_response_after_mavsdk_client_process_stops(self) -> None:
        decision = self.watchdog.client_process_stopped()

        self.assertFalse(decision.permitted)
        self.assertIsNone(decision.action)
        self.assertEqual(decision.fault.kind, RuntimeFaultKind.MAVSDK_CLIENT_PROCESS_STOPPED)
        self.assertTrue(decision.requires_external_failsafe)


if __name__ == "__main__":
    unittest.main()
