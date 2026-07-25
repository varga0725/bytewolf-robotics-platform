"""Safety checks for the launch-relative P0.v2 geofence contract.

The fence has to hold on *every* route to a flight command, not only the
hand-written CLIs. It did not: `MissionSafetyProfile` had no fence field, so the
MissionSpec compiler — the path the dashboard's map, the chat agent and the
Telegram gateway all take — built its gate from altitude and radius alone. The
tighter of the twin's two horizontal bounds was decorative on exactly the routes
an operator uses.
"""

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


class MissionSpecGeofenceTests(unittest.TestCase):
    """The fence must refuse on the MissionSpec route too, and say so."""

    def _profile(self):
        from brain.mission_spec.validation import load_mission_safety_profile
        from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH

        return load_mission_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)

    def _fence_edge_m(self, profile) -> float:
        assert profile.allowed_geofence is not None
        return max(north for north, _east in profile.allowed_geofence.vertices_m)

    def test_the_compiler_profile_carries_the_platform_fence(self) -> None:
        self.assertIsNotNone(self._profile().allowed_geofence)

    def test_a_waypoint_past_the_fence_but_inside_the_radius_is_refused(self) -> None:
        """The gap this closes: outside the fence, inside the radius.

        The radius check passed it and nothing else looked, so the dashboard
        reviewed it, wrote an approved plan, and offered it for launch.
        """
        from apps.api.point_mission import build_point_mission_spec
        from brain.mission_spec.validation import validate_and_compile_mission_spec

        profile = self._profile()
        beyond_fence_m = self._fence_edge_m(profile) + 50.0
        self.assertLess(beyond_fence_m, profile.max_radius_m, "the radius must not be what refuses")

        report = validate_and_compile_mission_spec(
            build_point_mission_spec(
                north_m=beyond_fence_m, east_m=0.0, altitude_m=10.0, profile=profile
            ),
            profile,
        )

        self.assertFalse(report.approved)
        self.assertIn("geofence", "; ".join(issue.message for issue in report.issues))

    def test_the_refusal_is_reported_not_raised(self) -> None:
        """A validator that throws is a validator the callers cannot use.

        Passing the fence to the compiler's gate alone turned an out-of-fence
        point into an uncaught SafetyViolation escaping the report — a 500 where
        the operator needed a named reason.
        """
        from apps.api.point_mission import build_point_mission_spec
        from brain.mission_spec.validation import validate_and_compile_mission_spec

        profile = self._profile()
        spec = build_point_mission_spec(
            north_m=self._fence_edge_m(profile) + 50.0, east_m=0.0, altitude_m=10.0, profile=profile
        )

        report = validate_and_compile_mission_spec(spec, profile)  # must not raise

        self.assertIsNone(report.mission)

    def test_a_sweep_whose_corners_leave_the_fence_is_refused(self) -> None:
        """A circle can sit inside the radius and still push past a square fence."""
        from apps.api.point_mission import build_survey_mission_spec
        from brain.mission_spec.validation import validate_and_compile_mission_spec

        profile = self._profile()
        report = validate_and_compile_mission_spec(
            build_survey_mission_spec(
                centre_north_m=self._fence_edge_m(profile) - 10.0,
                centre_east_m=0.0,
                radius_m=30.0,
                spacing_m=10.0,
                altitude_m=10.0,
                profile=profile,
            ),
            profile,
        )

        self.assertFalse(report.approved)
        self.assertIn("geofence", "; ".join(issue.message for issue in report.issues))

    def test_a_waypoint_inside_the_fence_still_compiles(self) -> None:
        from apps.api.point_mission import build_point_mission_spec
        from brain.mission_spec.validation import validate_and_compile_mission_spec

        profile = self._profile()
        report = validate_and_compile_mission_spec(
            build_point_mission_spec(
                north_m=self._fence_edge_m(profile) * 0.5, east_m=0.0, altitude_m=10.0,
                profile=profile,
            ),
            profile,
        )

        self.assertTrue(report.approved, [issue.message for issue in report.issues])


if __name__ == "__main__":
    unittest.main()
