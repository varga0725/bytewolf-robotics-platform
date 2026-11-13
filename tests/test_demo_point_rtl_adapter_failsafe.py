"""Demo adapter must recover from mid-mission failures without leaving the vehicle airborne."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.execution import MissionPhase
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class DemoAdapterFailsafeTests(unittest.IsolatedAsyncioTestCase):
    async def test_home_tolerance_failure_triggers_recovery_before_raising(self) -> None:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._global_position_sample = AsyncMock(
            side_effect=[
                MagicMock(latitude_deg=0.0, longitude_deg=0.0, absolute_altitude_m=100.0),
                MagicMock(latitude_deg=0.001, longitude_deg=0.001, absolute_altitude_m=100.0),
            ]
        )
        adapter._drone = MagicMock()
        adapter._drone.action.arm = AsyncMock()
        adapter._drone.action.takeoff = AsyncMock()
        adapter._drone.action.set_takeoff_altitude = AsyncMock()
        adapter._drone.action.set_return_to_launch_altitude = AsyncMock()
        adapter._drone.action.return_to_launch = AsyncMock()
        adapter.goto_relative_waypoint = AsyncMock()
        adapter.wait_until_waypoint_reached = AsyncMock()
        adapter._sleep_with_runtime_watchdog = AsyncMock()
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()
        adapter._demo_recover_airborne = AsyncMock(side_effect=lambda execution, *_args: execution)

        mission = MagicMock(
            takeoff=MagicMock(target_altitude_m=2.0),
            return_to_home=MagicMock(target_altitude_m=2.0),
            waypoints=(MagicMock(),),
            hover_duration_s=0.0,
            waypoint_tolerance_m=2.0,
            waypoint_timeout_s=30.0,
            landing_timeout_s=60.0,
            home_tolerance_m=1.0,
            takeoff_settle_seconds=0.0,
        )

        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified"}),
        ):
            with self.assertRaises(RuntimeError):
                await adapter.execute_demo_point_rtl_mission(mission)

        adapter._demo_recover_airborne.assert_awaited_once()

    async def test_sitl_check_failure_refuses_before_arming(self) -> None:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()

        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(side_effect=DemoPointRtlError("not sitl")),
        ):
            with self.assertRaises(DemoPointRtlError):
                await adapter.execute_demo_point_rtl_mission(MagicMock(waypoints=()))


if __name__ == "__main__":
    unittest.main()
