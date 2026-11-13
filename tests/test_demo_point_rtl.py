"""Monday demo mission: one point, RTL, quarantined execution by default."""

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
    DemoPointRtlConfig,
    DemoPointRtlError,
    authorize_demo_point_rtl,
    demo_point_rtl_evidence_document,
    expected_demo_point_rtl_phases,
    load_demo_point_rtl_config,
    require_simulation_target,
    validate_demo_point_rtl_progress,
)
from brain.mission.execution import MissionExecution, MissionEvent, MissionPhase
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class DemoPointRtlPhaseTests(unittest.TestCase):
    def test_expected_phase_sequence_allows_zero_hover(self) -> None:
        self.assertEqual(
            expected_demo_point_rtl_phases(hover_duration_s=0.0)[-2:],
            (MissionPhase.RETURNING, MissionPhase.COMPLETED),
        )

    def test_validate_progress_accepts_a_complete_demo_run(self) -> None:
        timestamps = datetime.now(UTC)
        execution = MissionExecution.empty()
        for phase in (
            MissionPhase.ARMING,
            *expected_demo_point_rtl_phases(hover_duration_s=0.0),
        ):
            execution = execution.transition(phase, timestamp=timestamps)
        validate_demo_point_rtl_progress(execution, hover_duration_s=0.0)


class DemoPointRtlTargetTests(unittest.TestCase):
    def test_non_simulation_targets_are_refused(self) -> None:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="physical_actuation",
                endpoint="serial:///dev/ttyACM0",
                profile=profile,
                operation=MISSION_TYPE,
                request_sha256="abc",
                safety_profile_sha256="def",
            )


class DemoPointRtlAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_disarms_after_rtl(self) -> None:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        completed = MissionExecution.empty()
        for phase in (
            MissionPhase.ARMING,
            MissionPhase.TAKING_OFF,
            MissionPhase.NAVIGATING,
            MissionPhase.HOVERING,
            MissionPhase.RETURNING,
            MissionPhase.COMPLETED,
        ):
            completed = completed.transition(phase, timestamp=datetime.now(UTC))
        adapter.execute_waypoints_return_to_home_mission = AsyncMock(return_value=completed)
        adapter._drone = MagicMock()
        adapter._drone.action.disarm = AsyncMock()

        await adapter.execute_demo_point_rtl_mission(MagicMock())

        adapter._drone.action.disarm.assert_awaited_once()


class DemoPointRtlApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.client = TestClient(create_app(telemetry))

    def tearDown(self) -> None:
        self.directory.cleanup()

    @patch.dict(os.environ, {}, clear=True)
    def test_demo_start_refuses_without_the_execution_gate(self) -> None:
        response = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "mission_execution_quarantined")

    @patch("apps.api.demo_mission.subprocess.Popen")
    @patch("apps.api.demo_mission.wait_for_free_link", return_value=True)
    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    def test_demo_start_submits_for_simulation_when_enabled(self, _wait, popen) -> None:
        process = MagicMock()
        process.pid = 4242
        process.poll.return_value = None
        popen.return_value = process
        response = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["mission_type"], MISSION_TYPE)
        command = popen.call_args.args[0]
        self.assertIn("brain.cli.fly_demo_point_rtl", command)


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
            document = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(document["mission_type"], MISSION_TYPE)
            self.assertEqual(document["overall_status"], "passed")


if __name__ == "__main__":
    unittest.main()
