import unittest

from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.safety.gate import FlightLimits, SafetyGate, SafetyViolation


class TakeoffSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=500.0))

    def test_accepts_a_safe_takeoff_command(self) -> None:
        command = TakeoffCommand(target_altitude_m=2.0)

        decision = self.gate.evaluate(command)

        self.assertTrue(decision.approved)
        self.assertEqual(decision.command, command)

    def test_rejects_takeoff_above_the_configured_limit(self) -> None:
        command = TakeoffCommand(target_altitude_m=20.1)

        with self.assertRaises(SafetyViolation):
            self.gate.evaluate(command)

    def test_rejects_non_positive_takeoff_altitude(self) -> None:
        for altitude in (0.0, -1.0):
            with self.subTest(altitude=altitude):
                with self.assertRaises(SafetyViolation):
                    self.gate.evaluate(TakeoffCommand(target_altitude_m=altitude))

    def test_rejects_non_finite_takeoff_altitude(self) -> None:
        for altitude in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(altitude=altitude):
                with self.assertRaises(SafetyViolation):
                    self.gate.evaluate(TakeoffCommand(target_altitude_m=altitude))

    def test_accepts_a_waypoint_inside_the_distance_and_altitude_limits(self) -> None:
        command = WaypointCommand(north_m=12.0, east_m=-5.0, target_altitude_m=3.0)

        decision = self.gate.evaluate(command)

        self.assertTrue(decision.approved)
        self.assertEqual(decision.command, command)

    def test_rejects_a_waypoint_beyond_the_distance_limit(self) -> None:
        command = WaypointCommand(north_m=500.0, east_m=1.0, target_altitude_m=3.0)

        with self.assertRaises(SafetyViolation):
            self.gate.evaluate(command)


if __name__ == "__main__":
    unittest.main()
