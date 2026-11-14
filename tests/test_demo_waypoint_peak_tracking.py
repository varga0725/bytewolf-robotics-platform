"""Peak altitude tracking must be started without un-awaited coroutine warnings."""

from __future__ import annotations

import asyncio
import unittest
import warnings
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.demo_waypoint_mission_adapter import execute_demo_waypoint_mission
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class DemoWaypointPeakTrackingTests(unittest.IsolatedAsyncioTestCase):
    async def test_peak_tracker_observes_streamed_altitudes(self) -> None:
        async def positions():
            for altitude in (0.0, 4.0, 8.07):
                yield MagicMock(relative_altitude_m=altitude)
                await asyncio.sleep(0)

        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        telemetry = MagicMock()
        telemetry.position = positions
        drone = MagicMock()
        drone.telemetry = telemetry
        adapter._drone = drone
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            await adapter._start_peak_altitude_tracking()
            await asyncio.sleep(0.05)
            await adapter._stop_peak_altitude_tracking()
        self.assertGreater(adapter._peak_relative_altitude_m, 0.0)

    async def test_execute_demo_mission_awaits_peak_tracking(self) -> None:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._global_position_sample = AsyncMock(
            return_value=MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()

        async def positions():
            for altitude in (0.0, 4.0, 8.07, 7.0, 0.0):
                yield MagicMock(relative_altitude_m=altitude)
                await asyncio.sleep(0)

        telemetry = MagicMock()
        telemetry.position = positions
        drone = MagicMock()
        drone.telemetry = telemetry
        mission = MagicMock()
        mission.set_return_to_launch_after_mission = AsyncMock()
        mission.upload_mission = AsyncMock()
        mission.start_mission = AsyncMock()
        mission.clear_mission = AsyncMock()

        class _Progress:
            def __init__(self, current: int, total: int) -> None:
                self.current = current
                self.total = total

        async def progress():
            yield _Progress(1, 2)
            yield _Progress(2, 2)

        mission.mission_progress = progress
        drone.mission = mission
        drone.action.arm = AsyncMock()
        adapter._drone = drone

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:1],
            mission_timeout_s=5.0,
            progress_stall_timeout_s=3.0,
        )
        tracker = AsyncMock(wraps=adapter._start_peak_altitude_tracking)
        adapter._start_peak_altitude_tracking = tracker
        with warnings.catch_warnings():
            warnings.simplefilter("error", RuntimeWarning)
            with patch(
                "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
                AsyncMock(return_value={"status": "verified", "matched_signals": ["a", "b"]}),
            ):
                await execute_demo_waypoint_mission(adapter, config, action_timeout_s=2.0)
        tracker.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
