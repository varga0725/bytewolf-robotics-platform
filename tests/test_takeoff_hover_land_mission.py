import unittest

from brain.mission.flight import (
    MissionValidationError,
    authorize_takeoff_hover_land,
    authorize_takeoff_return_to_home,
    authorize_takeoff_waypoint_land,
)
from brain.safety.gate import FlightLimits, SafetyGate, SafetyViolation


class TakeoffHoverLandMissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=500.0))

    def test_authorizes_a_safe_mission(self) -> None:
        mission = authorize_takeoff_hover_land(self.gate, target_altitude_m=2.0, hover_duration_s=5.0)

        self.assertEqual(mission.takeoff.target_altitude_m, 2.0)
        self.assertEqual(mission.hover_duration_s, 5.0)

    def test_rejects_an_unsafe_takeoff(self) -> None:
        with self.assertRaises(SafetyViolation):
            authorize_takeoff_hover_land(self.gate, target_altitude_m=21.0, hover_duration_s=5.0)

    def test_rejects_invalid_hover_duration(self) -> None:
        for duration in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(duration=duration):
                with self.assertRaises(MissionValidationError):
                    authorize_takeoff_hover_land(self.gate, target_altitude_m=2.0, hover_duration_s=duration)

    def test_authorizes_a_bounded_takeoff_waypoint_land_mission(self) -> None:
        mission = authorize_takeoff_waypoint_land(
            self.gate,
            takeoff_altitude_m=2.0,
            north_m=5.0,
            east_m=0.0,
            waypoint_altitude_m=2.0,
            hover_duration_s=3.0,
        )

        self.assertEqual(mission.waypoint.north_m, 5.0)
        self.assertEqual(mission.waypoint.target_altitude_m, 2.0)

    def test_authorizes_a_bounded_return_to_home_mission(self) -> None:
        mission = authorize_takeoff_return_to_home(
            self.gate,
            takeoff_altitude_m=2.0,
            hover_duration_s=3.0,
        )

        self.assertEqual(mission.takeoff.target_altitude_m, 2.0)
        self.assertEqual(mission.hover_duration_s, 3.0)
