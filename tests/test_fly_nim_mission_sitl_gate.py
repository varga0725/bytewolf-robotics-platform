"""fly_nim_mission --execute must verify SITL identity after connect."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import fly_nim_mission


class FlyNimMissionSitlGateTests(unittest.IsolatedAsyncioTestCase):
    async def test_execute_refuses_before_mission_when_sitl_check_fails(self) -> None:
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

        mission = MagicMock(vehicle_id="x500v2", source_hash="abc")

        adapter = MagicMock()
        adapter.connect = AsyncMock()
        adapter.preflight_telemetry = None

        with (
            patch.object(fly_nim_mission, "_load_approved_plan", return_value=({}, mission)),
            patch.object(fly_nim_mission, "load_safety_profile"),
            patch.object(fly_nim_mission, "authorize_actuation_connection"),
            patch.object(fly_nim_mission, "prepare_flight_run_recording"),
            patch.object(fly_nim_mission, "acquire_px4_link"),
            patch.object(fly_nim_mission, "MavsdkMissionAdapter", return_value=adapter),
            patch("mavsdk.System", MagicMock()),
            patch(
                "brain.cli.fly_nim_mission.require_sitl_before_arm",
                AsyncMock(side_effect=RuntimeError("SITL refused")),
            ),
            patch.object(fly_nim_mission, "execute_compiled_mission", AsyncMock()) as execute,
        ):
            with self.assertRaises(RuntimeError):
                await fly_nim_mission.run(arguments)
            execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()