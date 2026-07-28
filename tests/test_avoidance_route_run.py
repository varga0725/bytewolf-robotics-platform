import unittest

from simulation.control.avoidance_route_run import (
    _ACCEPTANCE_ROUTE_SPEED_M_S,
    _DETOUR_RELEASE_OFFSET_M,
    _REQUIRED_BYPASS_OFFSET_M,
    _SAFE_REJOIN_OVERSHOOT_M,
    _acceptance_profile,
    _wall_clearance,
)
from brain.safety.profile import load_safety_profile


class AvoidanceWallGeometryTests(unittest.TestCase):
    def test_acceptance_profile_tightens_the_generic_vehicle_speed(self) -> None:
        profile = load_safety_profile()

        acceptance = _acceptance_profile(profile)

        self.assertEqual(acceptance.max_speed_m_s, _ACCEPTANCE_ROUTE_SPEED_M_S)
        self.assertGreater(profile.max_speed_m_s, _ACCEPTANCE_ROUTE_SPEED_M_S)
    def test_bypass_offset_places_the_vehicle_outside_the_wall_clearance_envelope(self) -> None:
        self.assertEqual(_REQUIRED_BYPASS_OFFSET_M, 3.0)
        self.assertEqual(_wall_clearance(3.0, 12.0), 2.0)

    def test_detour_release_accounts_for_the_target_rejoin_diagonal(self) -> None:
        self.assertGreater(_DETOUR_RELEASE_OFFSET_M, _REQUIRED_BYPASS_OFFSET_M)
        self.assertEqual(_DETOUR_RELEASE_OFFSET_M, 6.5)

    def test_rejoin_overshoots_the_far_wall_edge_before_turning_home(self) -> None:
        # Rejoining at y=target - tolerance grazed the 2 m finite-wall envelope.
        self.assertGreater(_SAFE_REJOIN_OVERSHOOT_M, 0.0)

    def test_acceptance_does_not_treat_the_wall_corner_as_a_goal(self) -> None:
        # The runner uses a tight endpoint tolerance because the wall's safety
        # envelope extends past its finite collision box.
        import inspect
        from simulation.control.avoidance_route_run import _fly_avoidance_route
        self.assertIn("arrival_tolerance_m=0.2", inspect.getsource(_fly_avoidance_route))

    def test_clearance_is_measured_to_the_finite_wall_surface(self) -> None:
        self.assertEqual(_wall_clearance(0.0, 8.0), 3.0)
        self.assertEqual(_wall_clearance(0.0, 12.0), 0.0)


if __name__ == "__main__":
    unittest.main()
