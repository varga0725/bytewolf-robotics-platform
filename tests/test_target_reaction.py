"""Reacting to a target must propose a move and let the SafetyGate decide.

The reaction never commands; it hands a waypoint to the same gate that guards
every flight command. So the tests check both sides of the fail-closed boundary:
an untrusted or too-uncertain target reaches no proposal, and a proposal outside
the safety limits is refused by the gate, not by a softer check here.
"""

from datetime import UTC, datetime, timedelta
import math
import unittest

from brain.perception.target_estimator import TargetObservation
from brain.perception.target_reaction import react_to_target
from brain.safety.gate import FlightLimits, LocalPolygonGeofence, SafetyGate


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
_GEOFENCE = LocalPolygonGeofence(((-30, -30), (30, -30), (30, 30), (-30, 30)))
_GATE = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=50.0, allowed_geofence=_GEOFENCE))


def _target(
    north: float = 0.0,
    east: float = 5.0,
    *,
    validity: str = "valid",
    uncertainty: float = 0.15,
    captured_at: datetime = _NOW,
) -> TargetObservation:
    return TargetObservation(
        captured_at=captured_at,
        max_age_s=0.5,
        declared_validity=validity,
        label="landing-pad",
        confidence=0.95,
        offset_north_m=north,
        offset_east_m=east,
        range_m=10.0,
        horizontal_uncertainty_m=uncertainty,
        global_fix=None,
        source="down",
    )


class AcceptedReactionTests(unittest.TestCase):
    def test_a_trusted_target_becomes_a_gate_approved_waypoint(self) -> None:
        reaction = react_to_target(
            _target(north=0.0, east=5.0), vehicle_north_m=10.0, vehicle_east_m=0.0,
            gate=_GATE, now=_NOW, approach_altitude_m=5.0,
        )

        self.assertTrue(reaction.accepted)
        # The target offset is added to the vehicle's launch-relative position.
        self.assertEqual((reaction.waypoint.north_m, reaction.waypoint.east_m), (10.0, 5.0))
        self.assertEqual(reaction.waypoint.target_altitude_m, 5.0)
        self.assertEqual(reaction.target_label, "landing-pad")

    def test_the_offset_is_framed_from_the_vehicles_current_position(self) -> None:
        reaction = react_to_target(
            _target(north=3.0, east=-4.0), vehicle_north_m=-5.0, vehicle_east_m=2.0,
            gate=_GATE, now=_NOW, approach_altitude_m=4.0,
        )

        self.assertEqual((reaction.waypoint.north_m, reaction.waypoint.east_m), (-2.0, -2.0))


class FailClosedReactionTests(unittest.TestCase):
    def test_an_untrusted_target_reaches_no_proposal(self) -> None:
        for validity in ("invalid", "missing"):
            with self.subTest(validity=validity):
                reaction = react_to_target(
                    _target(validity=validity), vehicle_north_m=0.0, vehicle_east_m=0.0,
                    gate=_GATE, now=_NOW, approach_altitude_m=5.0,
                )
                self.assertFalse(reaction.accepted)
                self.assertIsNone(reaction.waypoint)

    def test_a_stale_target_is_not_acted_on(self) -> None:
        reaction = react_to_target(
            _target(captured_at=_NOW), vehicle_north_m=0.0, vehicle_east_m=0.0,
            gate=_GATE, now=_NOW + timedelta(seconds=1), approach_altitude_m=5.0,
        )

        self.assertFalse(reaction.accepted)
        self.assertIn("stale", reaction.rejection.reason)

    def test_a_too_uncertain_fix_is_refused_before_it_becomes_a_waypoint(self) -> None:
        reaction = react_to_target(
            _target(uncertainty=6.0), vehicle_north_m=0.0, vehicle_east_m=0.0,
            gate=_GATE, now=_NOW, approach_altitude_m=5.0, max_uncertainty_m=3.0,
        )

        self.assertFalse(reaction.accepted)
        self.assertEqual(reaction.rejection.detail, "horizontal_uncertainty")

    def test_missing_non_finite_or_negative_uncertainty_is_refused(self) -> None:
        cases = ((None, 3.0), (math.nan, 3.0), (-0.1, 3.0), (0.1, math.inf), (0.1, -1.0))
        for uncertainty, limit in cases:
            with self.subTest(uncertainty=uncertainty, limit=limit):
                reaction = react_to_target(
                    _target(uncertainty=uncertainty), vehicle_north_m=0.0, vehicle_east_m=0.0,
                    gate=_GATE, now=_NOW, approach_altitude_m=5.0, max_uncertainty_m=limit,
                )
                self.assertFalse(reaction.accepted)
                self.assertEqual(reaction.rejection.detail, "horizontal_uncertainty")

    def test_a_target_outside_the_geofence_is_refused_by_the_gate(self) -> None:
        """The gate, not a softer check here, is the authority on the geofence."""
        # Vehicle at north 28 m; a 5 m-north target puts the waypoint at 33 m,
        # outside the +/-30 m geofence but still inside the 50 m distance limit.
        reaction = react_to_target(
            _target(north=5.0, east=0.0), vehicle_north_m=28.0, vehicle_east_m=0.0,
            gate=_GATE, now=_NOW, approach_altitude_m=5.0,
        )

        self.assertFalse(reaction.accepted)
        self.assertEqual(reaction.rejection.detail, "safety_gate")
        self.assertIn("geofence", reaction.rejection.reason)

    def test_a_target_beyond_the_distance_limit_is_refused_by_the_gate(self) -> None:
        far_gate = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=5.0))
        reaction = react_to_target(
            _target(north=0.0, east=5.0), vehicle_north_m=10.0, vehicle_east_m=0.0,
            gate=far_gate, now=_NOW, approach_altitude_m=5.0,
        )

        self.assertFalse(reaction.accepted)
        self.assertEqual(reaction.rejection.detail, "safety_gate")

    def test_an_over_altitude_approach_is_refused_by_the_gate(self) -> None:
        reaction = react_to_target(
            _target(), vehicle_north_m=0.0, vehicle_east_m=0.0,
            gate=_GATE, now=_NOW, approach_altitude_m=25.0,
        )

        self.assertFalse(reaction.accepted)
        self.assertEqual(reaction.rejection.detail, "safety_gate")


class BoundaryTests(unittest.TestCase):
    def test_the_reaction_module_never_imports_a_flight_or_mavsdk_path(self) -> None:
        import ast
        from pathlib import Path

        import brain.perception.target_reaction as reaction

        tree = ast.parse(Path(reaction.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for module in imported:
            self.assertNotIn("mavsdk", module)
            self.assertNotIn("adapters", module)


if __name__ == "__main__":
    unittest.main()
