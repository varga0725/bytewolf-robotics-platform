"""Safety checks for the launch-relative P0.v2 geofence contract."""

from dataclasses import FrozenInstanceError
import unittest

from brain.mission.commands import WaypointCommand
from brain.safety.gate import FlightLimits, LocalPolygonGeofence, SafetyGate, SafetyViolation


class GeofenceSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fence = LocalPolygonGeofence(
            vertices_m=((0.0, 0.0), (20.0, 0.0), (20.0, 20.0), (0.0, 20.0))
        )
        self.gate = SafetyGate(
            FlightLimits(
                max_altitude_m=20.0,
                max_distance_m=50.0,
                allowed_geofence=self.fence,
            )
        )

    def test_accepts_waypoints_inside_or_on_the_geofence_boundary(self) -> None:
        for north_m, east_m in ((10.0, 10.0), (0.0, 5.0), (20.0, 20.0)):
            with self.subTest(north_m=north_m, east_m=east_m):
                decision = self.gate.evaluate(WaypointCommand(north_m, east_m, 2.0))

                self.assertTrue(decision.approved)

    def test_rejects_a_waypoint_outside_geofence_before_any_flight_command(self) -> None:
        with self.assertRaisesRegex(SafetyViolation, "geofence"):
            self.gate.evaluate(WaypointCommand(north_m=25.0, east_m=10.0, target_altitude_m=2.0))

    def test_rejects_invalid_polygon_vertices(self) -> None:
        with self.assertRaisesRegex(ValueError, "three"):
            LocalPolygonGeofence(vertices_m=((0.0, 0.0), (1.0, 1.0)))
        with self.assertRaisesRegex(ValueError, "finite"):
            LocalPolygonGeofence(
                vertices_m=((0.0, 0.0), (1.0, 0.0), (float("nan"), 1.0))
            )

    def test_geofence_is_an_immutable_contract(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.fence.vertices_m = ()  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
