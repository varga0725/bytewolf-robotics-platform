"""Lidar preflight runs only for waypoint-bearing NIM missions in simulation."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import fly_nim_mission
from brain.mission.commands import LandCommand, TakeoffCommand, WaypointCommand
from brain.mission_spec.validation import CompiledMission


def _compiled_mission(*commands: object) -> CompiledMission:
    return CompiledMission(
        mission_id="test-mission",
        vehicle_id="x500v2",
        source_hash="abc",
        commands=tuple(commands),  # type: ignore[arg-type]
        hold_durations_s=(),
        terminal_action="LAND",
        abort_policy=(),
    )


class FlyNimMissionLidarPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def _run_execute(self, mission: CompiledMission, *, require_lidar: MagicMock) -> None:
        arguments = MagicMock()
        arguments.command = None
        arguments.mission_spec_file = MagicMock()
        arguments.execute = True
        arguments.safety_profile = MagicMock()
        arguments.endpoint = "udpin://127.0.0.1:14540"
        arguments.deployment_mode = "simulation"
        arguments.physical_actuation_permit = None
        arguments.connection_timeout = 15.0
        arguments.preflight_wait_seconds = 120.0
        arguments.mavsdk_server_port = 50051
        arguments.artifact_dir = None
        arguments.dashboard_snapshot = MagicMock()
        arguments.telemetry_history = None
        arguments.no_world_memory = True

        adapter = MagicMock()
        adapter.connect = AsyncMock()
        protected = MagicMock()
        protected.require_lidar_scans = require_lidar

        with (
            patch.object(fly_nim_mission, "load_mission_safety_profile", return_value=MagicMock()),
            patch.object(fly_nim_mission, "_load_approved_plan", return_value=({}, mission)),
            patch.object(fly_nim_mission, "load_safety_profile", return_value=MagicMock(physical_actuation_enabled=False)),
            patch.object(fly_nim_mission, "authorize_actuation_connection"),
            patch.object(fly_nim_mission, "file_sha256", return_value="abc"),
            patch.object(
                fly_nim_mission,
                "prepare_flight_run_recording",
                return_value=MagicMock(run_id="test-run", telemetry_history_path=MagicMock()),
            ),
            patch.object(fly_nim_mission, "acquire_px4_link"),
            patch.object(fly_nim_mission, "write_run_artifact"),
            patch.object(fly_nim_mission, "stop_owned_mavsdk_server"),
            patch.object(fly_nim_mission, "MavsdkMissionAdapter", return_value=adapter),
            patch.object(fly_nim_mission, "SitlShieldedWaypointNavigator", return_value=protected),
            patch("mavsdk.System", MagicMock()),
            patch.object(fly_nim_mission, "require_sitl_before_arm", AsyncMock()),
            patch.object(fly_nim_mission, "execute_compiled_mission", AsyncMock(return_value=MagicMock(events=[]))),
            patch.object(fly_nim_mission, "MavsdkTelemetryRelay") as relay_ctor,
        ):
            relay_ctor.return_value.run = AsyncMock()
            await fly_nim_mission.run(arguments)

    async def test_takeoff_hold_land_skips_lidar_preflight(self) -> None:
        require_lidar = AsyncMock()
        mission = _compiled_mission(TakeoffCommand(2.0), LandCommand())
        await self._run_execute(mission, require_lidar=require_lidar)
        require_lidar.assert_not_called()

    async def test_waypoint_mission_requires_lidar_preflight(self) -> None:
        require_lidar = AsyncMock()
        mission = _compiled_mission(
            TakeoffCommand(2.0),
            WaypointCommand(5.0, 0.0, 2.0),
            LandCommand(),
        )
        await self._run_execute(mission, require_lidar=require_lidar)
        require_lidar.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()