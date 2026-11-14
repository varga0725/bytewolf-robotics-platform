"""Monday demo mission: review, approve, then execute on SITL."""

from __future__ import annotations

import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from apps.api.server import create_app
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.demo_point_rtl import (
    MISSION_TYPE,
    expected_demo_point_rtl_phases,
    validate_demo_point_rtl_progress,
)
from brain.mission.execution import MissionExecution, MissionPhase
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile

SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class DemoPointRtlPhaseTests(unittest.TestCase):
    def test_expected_phase_sequence_omits_hover_when_disabled(self) -> None:
        self.assertNotIn(MissionPhase.HOVERING, expected_demo_point_rtl_phases(hover_duration_s=0.0))

    def test_validate_progress_accepts_a_complete_demo_run(self) -> None:
        timestamps = datetime.now(UTC)
        execution = MissionExecution.empty()
        for phase in (MissionPhase.ARMING, *expected_demo_point_rtl_phases(hover_duration_s=0.0)):
            execution = execution.transition(phase, timestamp=timestamps)
        validate_demo_point_rtl_progress(execution, hover_duration_s=0.0)


class DemoPointRtlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.client = TestClient(create_app(telemetry))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_missing_session_header_is_rejected(self) -> None:
        response = self.client.post("/api/v1/missions/demo-point-rtl")
        self.assertEqual(response.status_code, 422)

    def test_invalid_session_is_rejected(self) -> None:
        response = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": "not-a-uuid"},
        )
        self.assertEqual(response.status_code, 400)

    @patch.dict(os.environ, {}, clear=True)
    def test_review_registers_a_plan_without_executing(self) -> None:
        response = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_required"])

    @patch.dict(os.environ, {}, clear=True)
    def test_approve_refuses_execution_when_quarantined(self) -> None:
        plan_id = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        ).json()["plan_id"]
        response = self.client.post(
            "/api/v1/plans/approve",
            json={"plan_id": plan_id},
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "mission_execution_quarantined")

    @patch("apps.gateway.plan_execution.subprocess.Popen")
    @patch("apps.gateway.plan_execution.wait_for_free_link", return_value=True)
    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    def test_approve_executes_after_review(self, _wait, popen) -> None:
        plan_id = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        ).json()["plan_id"]
        process = MagicMock(pid=99, poll=MagicMock(return_value=None))
        popen.return_value = process
        approved = self.client.post(
            "/api/v1/plans/approve",
            json={"plan_id": plan_id},
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(approved.status_code, 200)
        command = popen.call_args.args[0]
        self.assertIn("brain.cli.fly_demo_point_rtl", command)


class DemoPointRtlAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_disarms_after_rtl(self) -> None:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._require_preflight = AsyncMock()
        adapter._record = lambda execution, phase: execution.transition(phase, timestamp=datetime.now(UTC))
        adapter._global_position_sample = AsyncMock(
            return_value=MagicMock(latitude_deg=0.0, longitude_deg=0.0, absolute_altitude_m=100.0)
        )
        adapter._drone = MagicMock()
        for method in (
            "arm",
            "takeoff",
            "set_takeoff_altitude",
            "set_return_to_launch_altitude",
            "return_to_launch",
            "disarm",
        ):
            setattr(adapter._drone.action, method, AsyncMock())
        adapter.goto_relative_waypoint = AsyncMock()
        adapter.wait_until_waypoint_reached = AsyncMock()
        adapter._sleep_with_runtime_watchdog = AsyncMock()
        adapter.wait_until_landed = AsyncMock()
        adapter._demo_disarm_when_landed = AsyncMock()
        mission = MagicMock(
            takeoff=MagicMock(target_altitude_m=2.0),
            return_to_home=MagicMock(target_altitude_m=2.0),
            waypoints=(MagicMock(),),
            hover_duration_s=0.0,
            waypoint_tolerance_m=2.0,
            waypoint_timeout_s=30.0,
            landing_timeout_s=60.0,
            home_tolerance_m=6.0,
            takeoff_settle_seconds=0.0,
        )
        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(return_value={"status": "verified"}),
        ):
            await adapter.execute_demo_point_rtl_mission(mission)
        adapter._demo_disarm_when_landed.assert_awaited_once()


@unittest.skipUnless(os.environ.get("BYTEWOLF_SITL_ACCEPTANCE") == "1", "live PX4 SITL required")
class DemoPointRtlAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_live_sitl_reaches_every_phase_and_writes_evidence(self) -> None:
        from brain.cli import fly_demo_point_rtl

        with TemporaryDirectory() as directory:
            arguments = fly_demo_point_rtl.parse_arguments([])
            arguments.artifact_dir = Path(directory) / "artifacts"
            arguments.endpoint = os.environ.get("BYTEWOLF_PX4_ENDPOINT", "udpin://127.0.0.1:14540")
            with patch.dict(
                os.environ,
                {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true", "BYTEWOLF_SITL_ACCEPTANCE": "1"},
            ):
                await fly_demo_point_rtl.run(arguments)
            evidence_path = Path("simulation/artifacts/demo-point-rtl/latest.json")
            self.assertTrue(evidence_path.is_file())


if __name__ == "__main__":
    unittest.main()
