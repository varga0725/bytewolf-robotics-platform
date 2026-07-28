import unittest

from simulation.control.avoidance_route_run import (
    _DETOUR_RELEASE_OFFSET_M,
    _REQUIRED_BYPASS_OFFSET_M,
    _wall_clearance,
)


class AvoidanceWallGeometryTests(unittest.TestCase):
    def test_bypass_offset_places_the_vehicle_outside_the_wall_clearance_envelope(self) -> None:
        self.assertEqual(_REQUIRED_BYPASS_OFFSET_M, 3.0)
        self.assertEqual(_wall_clearance(3.0, 12.0), 2.0)

    def test_detour_release_accounts_for_the_target_rejoin_diagonal(self) -> None:
        self.assertGreater(_DETOUR_RELEASE_OFFSET_M, _REQUIRED_BYPASS_OFFSET_M)
        self.assertEqual(_DETOUR_RELEASE_OFFSET_M, 6.5)

    def test_clearance_is_measured_to_the_finite_wall_surface(self) -> None:
        self.assertEqual(_wall_clearance(0.0, 8.0), 3.0)
        self.assertEqual(_wall_clearance(0.0, 12.0), 0.0)


if __name__ == "__main__":
    unittest.main()
