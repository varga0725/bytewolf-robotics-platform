"""End-to-end fly_demo_waypoint_mission acceptance evidence on latest.json."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import fly_demo_waypoint_mission
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.mission.demo_waypoint_reviewed_plan import write_reviewed_demo_waypoint_plan
from brain.mission.execution import MissionExecution, MissionPhase
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class FlyDemoWaypointEvidenceE2ETests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {
                "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
                "BYTEWOLF_SITL_ACCEPTANCE": "1",
            },
            clear=False,
        )
        self.env_patch.start()
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env_patch.stop)
        self.evidence_dir = Path("simulation/artifacts/demo-waypoint-mission")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._saved_latest = None
        latest = self.evidence_dir / "latest.json"
        if latest.is_file():
            self._saved_latest = latest.read_text(encoding="utf-8")
        for path in self.evidence_dir.glob("demo-waypoint-mission-*.json"):
            path.unlink(missing_ok=True)
        for path in self.evidence_dir.glob("trajectory-*.png"):
            path.unlink(missing_ok=True)
        latest.unlink(missing_ok=True)

    def tearDown(self) -> None:
        latest = self.evidence_dir / "latest.json"
        latest.unlink(missing_ok=True)
        if self._saved_latest is not None:
            latest.write_text(self._saved_latest, encoding="utf-8")

    def _completed_execution(self) -> MissionExecution:
        execution = MissionExecution.empty()
        for phase in (
            MissionPhase.ARMING,
            MissionPhase.TAKING_OFF,
            MissionPhase.NAVIGATING,
            MissionPhase.RETURNING,
            MissionPhase.COMPLETED,
        ):
            execution = execution.transition(phase, timestamp=datetime.now(UTC))
        return execution

    def _arguments(self, artifact_dir: Path) -> tuple[MagicMock, DemoWaypointMissionConfig, Path]:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:2],
        )
        plan_dir = Path(self.tmp.name) / "plans"
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_path = plan_dir / "mission.demo-waypoint-mission.json"
        write_reviewed_demo_waypoint_plan(plan_path, config, mission_id="e2e-plan")
        arguments = fly_demo_waypoint_mission.parse_arguments(
            [
                "--endpoint",
                "udpin://127.0.0.1:14540",
                "--deployment-mode",
                "simulation",
                "--skip-optional-screen-capture",
                "--artifact-dir",
                str(artifact_dir),
                "--plan-file",
                str(plan_path),
            ]
        )
        return arguments, config, plan_path

    async def test_completed_run_leaves_single_passed_latest(self) -> None:
        artifact_dir = Path(self.tmp.name) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        arguments, config, plan_path = self._arguments(artifact_dir)
        run_id = "e2e-success-run"
        adapter = MagicMock()
        adapter._demo_sitl_proof = {"status": "verified"}
        adapter._demo_mission_items_reached = [{"index": 2, "total": 2}]
        adapter._peak_relative_altitude_m = 8.0
        adapter._demo_disarm_reason = None
        adapter.preflight_telemetry = None

        execution = self._completed_execution()

        async def fake_sample(*_args, **_kwargs) -> None:
            return None

        with (
            patch(
                "brain.cli.fly_demo_waypoint_mission.load_demo_waypoint_plan_config",
                return_value=config,
            ),
            patch("brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval"),
            patch(
                "brain.cli.fly_demo_waypoint_mission.load_safety_profile",
                return_value=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.file_sha256", return_value="abc"),
            patch("brain.cli.fly_demo_waypoint_mission.authorize_demo_waypoint_mission", return_value=config),
            patch("brain.cli.fly_demo_waypoint_mission.require_simulation_target"),
            patch("brain.cli.fly_demo_waypoint_mission.acquire_px4_link"),
            patch("brain.cli.fly_demo_waypoint_mission.stop_owned_mavsdk_server"),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkMissionAdapter", return_value=adapter),
            patch.object(adapter, "connect", AsyncMock()),
            patch(
                "brain.cli.fly_demo_waypoint_mission.execute_demo_waypoint_mission",
                AsyncMock(return_value=execution),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.validate_demo_waypoint_mission_progress"),
            patch("brain.cli.fly_demo_waypoint_mission.sample_positions_while", side_effect=fake_sample),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkTelemetryRelay", _FakeTelemetryRelay),
            patch(
                "brain.cli.fly_demo_waypoint_mission.prepare_flight_run_recording",
                return_value=MagicMock(run_id=run_id, telemetry_history_path=artifact_dir / "hist.jsonl"),
            ),
            patch(
                "brain.cli.fly_demo_waypoint_mission._resolve_ulog_evidence",
                return_value=("px4-ulogs/test.ulg", "captured", "deadbeef"),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.write_run_artifact", return_value=artifact_dir / "audit.json"),
            patch("brain.cli.fly_demo_waypoint_mission.render_demo_trajectory_png"),
        ):
            system = MagicMock()
            home = MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
            system.telemetry.home = MagicMock(side_effect=lambda: _home_iter(home))
            with patch("mavsdk.System", return_value=system):
                await fly_demo_waypoint_mission.run(arguments)

        latest = json.loads((self.evidence_dir / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["overall_status"], "passed")
        run_files = sorted(self.evidence_dir.glob("demo-waypoint-mission-*.json"))
        self.assertEqual(len(run_files), 1)
        self.assertEqual(json.loads(run_files[0].read_text(encoding="utf-8"))["overall_status"], "passed")

    async def test_failed_run_writes_failed_latest_once(self) -> None:
        artifact_dir = Path(self.tmp.name) / "artifacts-fail"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        arguments, config, plan_path = self._arguments(artifact_dir)
        run_id = "e2e-fail-run"
        adapter = MagicMock()
        adapter._demo_sitl_proof = {}
        adapter._demo_mission_items_reached = []
        adapter._peak_relative_altitude_m = None
        adapter._demo_disarm_reason = None
        adapter.preflight_telemetry = None

        with (
            patch(
                "brain.cli.fly_demo_waypoint_mission.load_demo_waypoint_plan_config",
                return_value=config,
            ),
            patch("brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval"),
            patch(
                "brain.cli.fly_demo_waypoint_mission.load_safety_profile",
                return_value=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.file_sha256", return_value="abc"),
            patch("brain.cli.fly_demo_waypoint_mission.authorize_demo_waypoint_mission", return_value=config),
            patch("brain.cli.fly_demo_waypoint_mission.require_simulation_target"),
            patch("brain.cli.fly_demo_waypoint_mission.acquire_px4_link"),
            patch("brain.cli.fly_demo_waypoint_mission.stop_owned_mavsdk_server"),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkMissionAdapter", return_value=adapter),
            patch.object(adapter, "connect", AsyncMock()),
            patch(
                "brain.cli.fly_demo_waypoint_mission.execute_demo_waypoint_mission",
                AsyncMock(side_effect=RuntimeError("mission failed")),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.sample_positions_while", AsyncMock()),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkTelemetryRelay", _FakeTelemetryRelay),
            patch(
                "brain.cli.fly_demo_waypoint_mission.prepare_flight_run_recording",
                return_value=MagicMock(run_id=run_id, telemetry_history_path=artifact_dir / "hist.jsonl"),
            ),
            patch(
                "brain.cli.fly_demo_waypoint_mission._resolve_ulog_evidence",
                return_value=(None, "missing", None),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.write_run_artifact", return_value=artifact_dir / "audit.json"),
            patch("brain.cli.fly_demo_waypoint_mission.render_demo_trajectory_png"),
        ):
            system = MagicMock()
            home = MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
            system.telemetry.home = MagicMock(side_effect=lambda: _home_iter(home))
            with patch("mavsdk.System", return_value=system):
                with self.assertRaises(RuntimeError):
                    await fly_demo_waypoint_mission.run(arguments)

        latest = json.loads((self.evidence_dir / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["overall_status"], "failed")
        self.assertIn("mission failed", str(latest.get("failure_reason")))


async def _home_iter(home: MagicMock):
    yield home


class _FakeTelemetryRelay:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, stop_event: asyncio.Event) -> None:
        await stop_event.wait()


if __name__ == "__main__":
    unittest.main()
