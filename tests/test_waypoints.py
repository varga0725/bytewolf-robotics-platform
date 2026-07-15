import unittest

from brain.mission.commands import WaypointCommand
from brain.navigation.waypoints import GlobalPosition, relative_waypoint_to_global


class RelativeWaypointTests(unittest.TestCase):
    def test_converts_north_and_east_metres_to_a_global_target(self) -> None:
        origin = GlobalPosition(latitude_deg=47.5, longitude_deg=19.1, absolute_altitude_m=120.0)
        command = WaypointCommand(north_m=111.195, east_m=0.0, target_altitude_m=5.0)

        target = relative_waypoint_to_global(origin, command, current_relative_altitude_m=2.0)

        self.assertAlmostEqual(target.latitude_deg, 47.501, places=5)
        self.assertAlmostEqual(target.longitude_deg, 19.1, places=7)
        self.assertAlmostEqual(target.absolute_altitude_m, 123.0, places=5)
