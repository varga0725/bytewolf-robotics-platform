"""The point-mission route refuses before takeoff when no lidar scan arrives."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from brain.adapters.sitl_shielded_waypoint import SitlShieldedWaypointNavigator

TOPIC = "/world/baylands/model/x500_mono_cam_down_0/link/link/sensor/lidar_2d_v2/scan"


class LidarPreflightTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.navigator = SitlShieldedWaypointNavigator(drone=None, profile=None)
        self.enterContext(patch("brain.adapters.sitl_shielded_waypoint.discover_scan_topic", return_value=TOPIC))

    async def test_a_received_scan_passes(self) -> None:
        reply = SimpleNamespace(stdout='{"header": {}}\n')
        with patch("brain.adapters.sitl_shielded_waypoint.subprocess.run", return_value=reply) as run:
            self.assertEqual(await self.navigator.require_lidar_scans(), TOPIC)
        self.assertIn("-n", run.call_args.args[0])

    async def test_no_scan_is_refused_before_takeoff(self) -> None:
        outcomes = (
            SimpleNamespace(stdout=""),
            subprocess.TimeoutExpired(cmd="gz", timeout=5.0),
        )
        for outcome in outcomes:
            kwargs = {"side_effect": outcome} if isinstance(outcome, Exception) else {"return_value": outcome}
            with self.subTest(outcome=type(outcome).__name__), patch(
                "brain.adapters.sitl_shielded_waypoint.subprocess.run", **kwargs
            ), self.assertRaises(RuntimeError) as error:
                await self.navigator.require_lidar_scans()
            self.assertIn("refused before takeoff", str(error.exception))


if __name__ == "__main__":
    unittest.main()
