"""Demo adapter recovery must command RTL/land/disarm without transition crashes."""

from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.execution import MissionPhase
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def _position(*, lat: float = 0.0, lon: float = 0.0, alt: float = 100.0) -> MagicMock:
    return MagicMock(latitude_deg=lat, longitude_deg=lon, absolute_altitude_m=alt)


class DemoAdapterFailsafeTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self) -> MavsdkMissionAdapter:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._global_position_sample = AsyncMock(return_value=_position())
        adapter._drone = MagicMock()
        adapter._drone.action.arm = AsyncMock()
        adapter._drone.action.takeoff = AsyncMock()
        adapter._drone.action.land = AsyncMock()
        adapter._drone.action.disarm = AsyncMock()
        adapter._drone.action.set_takeoff_altitude = AsyncMock()
        adapter._drone.action.set_return_to_launch_altitude = AsyncMock()
        adapter._drone.action.return_to_launch = AsyncMock()
        adapter.goto_relative_waypoint = AsyncMock()
        adapter.wait_until_waypoint_reached = AsyncMock()
        adapter._sleep_with_runtime_watchdog = AsyncMock()
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()
        return adapter

    def _mission(self, *, hover_duration_s: float = 0.0) -> MagicMock:
        return MagicMock(
            takeoff=MagicMock(target_altitude_m=2.0),
            return_to_home=MagicMock(target_altitude_m=2.0),
            waypoints=(MagicMock(),),
            hover_duration_s=hover_duration_s,
            waypoint_tolerance_m=2.0,
            waypoint_timeout_s=30.0,
            landing_timeout_s=60.0,
            home_tolerance_m=50.0,
            takeoff_settle_seconds=1.0,
        )

    async def _run_expecting_failure(self, adapter: MavsdkMissionAdapter, mission: MagicMock) -> None:
        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified"}),
        ):
            with self.assertRaises(Exception):
                await adapter.execute_demo_point_rtl_mission(mission)

    async def test_recovery_from_taking_off_settle_failure_commands_rtl_and_disarm(self) -> None:
        adapter = self._adapter()
        adapter._sleep_with_runtime_watchdog = AsyncMock(side_effect=RuntimeError("settle watchdog"))

        await self._run_expecting_failure(adapter, self._mission())

        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_recovery_from_navigating_failure_commands_rtl_and_disarm(self) -> None:
        adapter = self._adapter()
        adapter.wait_until_waypoint_reached = AsyncMock(side_effect=TimeoutError("waypoint"))

        await self._run_expecting_failure(adapter, self._mission())

        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_recovery_from_hovering_failure_commands_rtl_and_disarm(self) -> None:
        adapter = self._adapter()
        adapter._sleep_with_runtime_watchdog = AsyncMock(
            side_effect=[None, RuntimeError("hover watchdog")]
        )

        await self._run_expecting_failure(adapter, self._mission(hover_duration_s=3.0))

        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_recovery_from_returning_failure_still_attempts_land_fallback(self) -> None:
        adapter = self._adapter()
        adapter.wait_until_landed = AsyncMock(side_effect=TimeoutError("landing"))

        await self._run_expecting_failure(adapter, self._mission())

        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._drone.action.land.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_takeoff_failure_after_arm_attempts_disarm(self) -> None:
        adapter = self._adapter()
        adapter._drone.action.takeoff = AsyncMock(side_effect=RuntimeError("takeoff rejected"))

        await self._run_expecting_failure(adapter, self._mission())

        adapter._drone.action.return_to_launch.assert_not_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_home_tolerance_failure_triggers_recovery_before_raising(self) -> None:
        adapter = self._adapter()
        adapter._global_position_sample = AsyncMock(
            side_effect=[
                _position(),
                _position(lat=0.01, lon=0.01),
            ]
        )

        await self._run_expecting_failure(adapter, self._mission())

        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited_once()

    async def test_sitl_check_failure_refuses_before_arming(self) -> None:
        adapter = self._adapter()

        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(side_effect=DemoPointRtlError("not sitl")),
        ):
            with self.assertRaises(DemoPointRtlError):
                await adapter.execute_demo_point_rtl_mission(MagicMock(waypoints=()))

        adapter._drone.action.arm.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
