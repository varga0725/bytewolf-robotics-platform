"""Unit tests for the SITL shielded waypoint navigator (no live simulator)."""

from __future__ import annotations

import asyncio
import inspect
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.offboard_route_executor import RouteState
from brain.adapters.sitl_shielded_waypoint import SitlShieldedWaypointNavigator
from brain.mission.commands import WaypointCommand
from brain.navigation.waypoints import GlobalPosition
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class SitlShieldedWaypointNavigatorTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        self.drone = MagicMock()
        position = MagicMock()
        position.latitude_deg = 47.0
        position.longitude_deg = 19.0
        position.relative_altitude_m = 2.0
        position.absolute_altitude_m = 102.0
        attitude = MagicMock(yaw_deg=0.0)

        async def positions():
            yield position

        async def attitudes():
            yield attitude

        self.drone.telemetry.position.return_value = positions()
        self.drone.telemetry.attitude_euler.return_value = attitudes()

    async def test_speed_is_capped_at_one_metre_per_second(self) -> None:
        captured: dict[str, object] = {}

        class RecordingExecutor:
            def __init__(self, **kwargs) -> None:
                captured["profile"] = kwargs["profile"]
                self.run = AsyncMock()

        with (
            patch("brain.adapters.sitl_shielded_waypoint.discover_scan_topic", return_value="/scan"),
            patch("brain.adapters.sitl_shielded_waypoint.subprocess.Popen") as popen,
            patch("brain.adapters.sitl_shielded_waypoint.OffboardRouteExecutor", RecordingExecutor),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.sleep", new=AsyncMock()),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.to_thread", new=AsyncMock(return_value=0)),
            patch("brain.adapters.sitl_shielded_waypoint.MavsdkVelocityAdapter"),
            patch("brain.adapters.sitl_shielded_waypoint.OffboardFallbackExecutor"),
            patch("brain.adapters.sitl_shielded_waypoint._enabled", side_effect=lambda value: value),
        ):
            process = MagicMock()
            process.wait.return_value = 0
            popen.return_value = process
            navigator = SitlShieldedWaypointNavigator(self.drone, self.profile)
            command = WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0)
            await navigator(command)

        active = captured["profile"]
        self.assertLessEqual(active.max_speed_m_s, 1.0)
        self.assertEqual(active.max_speed_m_s, min(1.0, self.profile.max_speed_m_s))

    async def test_replanner_uses_the_route_envelope_detour_bound(self) -> None:
        captured: dict[str, object] = {}

        class RecordingExecutor:
            def __init__(self, **kwargs) -> None:
                captured["replanner"] = kwargs["replanner"]
                self.run = AsyncMock()

        with (
            patch("brain.adapters.sitl_shielded_waypoint.discover_scan_topic", return_value="/scan"),
            patch("brain.adapters.sitl_shielded_waypoint.subprocess.Popen") as popen,
            patch("brain.adapters.sitl_shielded_waypoint.OffboardRouteExecutor", RecordingExecutor),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.sleep", new=AsyncMock()),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.to_thread", new=AsyncMock(return_value=0)),
            patch("brain.adapters.sitl_shielded_waypoint.MavsdkVelocityAdapter"),
            patch("brain.adapters.sitl_shielded_waypoint.OffboardFallbackExecutor"),
            patch("brain.adapters.sitl_shielded_waypoint._enabled", side_effect=lambda value: value),
        ):
            process = MagicMock()
            process.wait.return_value = 0
            popen.return_value = process
            navigator = SitlShieldedWaypointNavigator(self.drone, self.profile)
            await navigator(WaypointCommand(north_m=3.0, east_m=1.0, target_altitude_m=2.0))

        replanner = captured["replanner"]
        self.assertEqual(replanner._max_detour_s, 45.0)

    async def test_lidar_capture_and_shield_dependencies_are_required(self) -> None:
        enabled_calls: list[object] = []

        class RecordingExecutor:
            def __init__(self, **kwargs) -> None:
                self.run = AsyncMock()

        with (
            patch("brain.adapters.sitl_shielded_waypoint.discover_scan_topic", return_value="/lidar"),
            patch("brain.adapters.sitl_shielded_waypoint.subprocess.Popen") as popen,
            patch("brain.adapters.sitl_shielded_waypoint.OffboardRouteExecutor", RecordingExecutor),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.sleep", new=AsyncMock()),
            patch("brain.adapters.sitl_shielded_waypoint.asyncio.to_thread", new=AsyncMock(return_value=0)),
            patch("brain.adapters.sitl_shielded_waypoint.MavsdkVelocityAdapter"),
            patch("brain.adapters.sitl_shielded_waypoint.OffboardFallbackExecutor"),
            patch(
                "brain.adapters.sitl_shielded_waypoint._enabled",
                side_effect=lambda value: enabled_calls.append(value) or value,
            ),
        ):
            process = MagicMock()
            process.wait.return_value = 0
            popen.return_value = process
            navigator = SitlShieldedWaypointNavigator(
                self.drone, self.profile, scan_path=Path("var/runtime/test-lidar-scans.json")
            )
            await navigator(WaypointCommand(north_m=2.0, east_m=0.0, target_altitude_m=2.0))

        popen.assert_called_once()
        gz_args = popen.call_args.args[0]
        self.assertEqual(gz_args[0], "gz")
        self.assertIn("/lidar", gz_args)
        self.assertEqual(enabled_calls, [self.profile.offboard, self.profile.shield])
        from brain.adapters import sitl_shielded_waypoint as module

        obstacle_source = inspect.getsource(module._ObstacleSource)
        self.assertIn("_latest_observation", obstacle_source)


if __name__ == "__main__":
    unittest.main()
