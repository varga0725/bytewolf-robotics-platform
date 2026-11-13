"""Unmocked adapter recovery: arm/start failures use in_air telemetry."""

from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.demo_waypoint_mission_adapter import execute_demo_waypoint_mission
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


def _in_air_stream(*values: bool) -> MagicMock:
    async def _iter():
        for value in values:
            yield value
        while True:
            await asyncio.sleep(3600.0)
            yield values[-1]

    return MagicMock(side_effect=_iter)


class DemoWaypointRecoveryTests(unittest.IsolatedAsyncioTestCase):
    def _config(self) -> DemoWaypointMissionConfig:
        return DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:1],
            mission_timeout_s=5.0,
            progress_stall_timeout_s=3.0,
            upload_timeout_s=2.0,
            rtl_timeout_s=30.0,
            landing_timeout_s=5.0,
        )

    def _adapter(self) -> MavsdkMissionAdapter:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._global_position_sample = AsyncMock(
            return_value=SimpleNamespace(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        )
        adapter._start_peak_altitude_tracking = AsyncMock()
        adapter._stop_peak_altitude_tracking = AsyncMock()
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()
        return adapter

    async def test_start_mission_timeout_in_air_triggers_rtl(self) -> None:
        adapter = self._adapter()
        mission = MagicMock()
        mission.set_return_to_launch_after_mission = AsyncMock()
        mission.upload_mission = AsyncMock()
        mission.start_mission = AsyncMock(side_effect=asyncio.TimeoutError())
        mission.pause_mission = AsyncMock()
        mission.clear_mission = AsyncMock()
        adapter._drone = MagicMock()
        adapter._drone.mission = mission
        adapter._drone.action.arm = AsyncMock()
        adapter._drone.action.return_to_launch = AsyncMock()
        adapter._drone.action.land = AsyncMock()
        adapter._drone.telemetry.in_air = _in_air_stream(True)
        adapter._drone.action.disarm = AsyncMock()
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": ["sim_gz_en", "product_name"]}),
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await execute_demo_waypoint_mission(adapter, self._config(), action_timeout_s=0.01)
        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._demo_disarm_when_landed.assert_awaited()
        adapter._drone.action.disarm.assert_not_awaited()

    async def test_start_mission_timeout_unknown_in_air_skips_disarm(self) -> None:
        adapter = self._adapter()
        mission = MagicMock()
        mission.set_return_to_launch_after_mission = AsyncMock()
        mission.upload_mission = AsyncMock()
        mission.start_mission = AsyncMock(side_effect=asyncio.TimeoutError())
        mission.pause_mission = AsyncMock()
        mission.clear_mission = AsyncMock()
        adapter._drone = MagicMock()
        adapter._drone.mission = mission
        adapter._drone.action.arm = AsyncMock()
        adapter._drone.action.return_to_launch = AsyncMock()
        adapter._drone.action.land = AsyncMock()
        adapter._drone.action.disarm = AsyncMock()
        async def _hang_forever():
            await asyncio.sleep(3600.0)
            yield True

        adapter._drone.telemetry.in_air = MagicMock(return_value=_hang_forever())
        adapter._drone.telemetry.landed_state = MagicMock(return_value=_hang_forever())

        async def disarm_refuses(*_args, **_kwargs):
            raise RuntimeError("Demo disarm refused: in_air stream stalled without ground confirmation.")

        adapter._demo_disarm_when_landed = AsyncMock(side_effect=disarm_refuses)
        with patch(
            "brain.adapters.demo_waypoint_mission_adapter.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified", "matched_signals": ["sim_gz_en", "product_name"]}),
        ):
            with self.assertRaises(asyncio.TimeoutError):
                await execute_demo_waypoint_mission(adapter, self._config(), action_timeout_s=0.01)
        adapter._drone.action.return_to_launch.assert_awaited()
        adapter._drone.action.disarm.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
