"""Console-spawned demo-waypoint Gazebo camera path (fakes only, no gz/MAVLink)."""

from __future__ import annotations

import asyncio
import json
import os
import unittest
from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import fly_demo_waypoint_mission
from brain.mission.demo_gz_scene import console_spawned_demo_gz_visual_eligible
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.mission.execution import MissionExecution, MissionPhase
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class ConsoleDemoGzVisualEligibilityTests(unittest.TestCase):
    def test_non_simulation_deployment_is_ineligible(self) -> None:
        self.assertFalse(
            console_spawned_demo_gz_visual_eligible(
                sitl_acceptance=False,
                deployment_mode="ground",
            )
        )

    def test_sitl_acceptance_is_ineligible(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"}, clear=False):
            self.assertFalse(
                console_spawned_demo_gz_visual_eligible(
                    sitl_acceptance=True,
                    deployment_mode="simulation",
                )
            )


class FlyDemoWaypointConsoleCameraTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.env_patch = patch.dict(
            os.environ,
            {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"},
            clear=False,
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

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

    async def _run_console_mission(
        self,
        env_overrides: dict[str, str] | None,
        *,
        gz_binary: str | None = "/usr/bin/gz",
        director: MagicMock | None = None,
        clear_camera_mode: bool = False,
    ) -> dict[str, object]:
        artifact_dir = Path(self.tmp.name) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:2],
        )
        arguments = fly_demo_waypoint_mission.parse_arguments(
            [
                "--endpoint",
                "udpin://127.0.0.1:14540",
                "--deployment-mode",
                "simulation",
                "--artifact-dir",
                str(artifact_dir),
            ]
        )
        run_id = "console-camera-run"
        adapter = MagicMock()
        adapter._demo_sitl_proof = {"status": "verified"}
        adapter._demo_mission_items_reached = [{"index": 2, "total": 2}]
        adapter._peak_relative_altitude_m = 8.0
        adapter._demo_disarm_reason = None
        adapter.preflight_telemetry = None
        execution = self._completed_execution()

        fake_director = director or MagicMock()
        if director is None:
            fake_director.apply_scene = AsyncMock()
            fake_director.reaffirm_camera_follow_after_arm = MagicMock()
            fake_director.reaffirm_camera_follow_at_rtl = MagicMock()
        hook_capture: dict[str, object] = {}

        async def execute_stub(_adapter, _config, *, on_armed=None, on_returning=None, **_kwargs):
            hook_capture["on_armed"] = on_armed
            hook_capture["on_returning"] = on_returning
            return execution

        artifact_paths: list[Path] = []

        def write_artifact_stub(*_args, **_kwargs):
            path = artifact_dir / f"{run_id}.json"
            path.write_text(json.dumps({"run_id": run_id, "outcome": "completed"}) + "\n", encoding="utf-8")
            artifact_paths.append(path)
            return path

        async def fake_sample(*_args, **_kwargs) -> None:
            return None

        capture_construct = MagicMock()
        ulog_wait = AsyncMock()
        director_ctor = MagicMock(return_value=fake_director)

        merged_env = dict(os.environ)
        merged_env.pop("BYTEWOLF_SITL_ACCEPTANCE", None)
        if clear_camera_mode:
            merged_env.pop("BYTEWOLF_DEMO_GZ_CAMERA_MODE", None)
        if env_overrides is not None:
            merged_env.update(env_overrides)

        system = MagicMock()
        home = MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        system.telemetry.home = MagicMock(side_effect=lambda: _home_iter(home))

        patches = [
            patch.dict(os.environ, merged_env, clear=True),
            patch("brain.cli.fly_demo_waypoint_mission.load_safety_profile", return_value=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)),
            patch("brain.cli.fly_demo_waypoint_mission.file_sha256", return_value="abc"),
            patch("brain.cli.fly_demo_waypoint_mission.authorize_demo_waypoint_mission", return_value=config),
            patch("brain.cli.fly_demo_waypoint_mission.require_simulation_target"),
            patch("brain.cli.fly_demo_waypoint_mission.acquire_px4_link"),
            patch("brain.cli.fly_demo_waypoint_mission.stop_owned_mavsdk_server"),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkMissionAdapter", return_value=adapter),
            patch.object(adapter, "connect", AsyncMock()),
            patch("brain.cli.fly_demo_waypoint_mission.execute_demo_waypoint_mission", side_effect=execute_stub),
            patch("brain.cli.fly_demo_waypoint_mission.validate_demo_waypoint_mission_progress"),
            patch("brain.cli.fly_demo_waypoint_mission.sample_positions_while", side_effect=fake_sample),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkTelemetryRelay", _FakeTelemetryRelay),
            patch(
                "brain.cli.fly_demo_waypoint_mission.prepare_flight_run_recording",
                return_value=MagicMock(run_id=run_id, telemetry_history_path=artifact_dir / "hist.jsonl"),
            ),
            patch("brain.cli.fly_demo_waypoint_mission.write_run_artifact", side_effect=write_artifact_stub),
            patch("brain.cli.fly_demo_waypoint_mission.DemoFlightScreenCapture", capture_construct),
            patch("brain.cli.fly_demo_waypoint_mission.wait_for_px4_ulog_archive", ulog_wait),
            patch("brain.mission.demo_gz_scene.gz_binary", return_value=gz_binary),
            patch("brain.mission.demo_gz_scene.DemoGzVisualDirector", director_ctor),
            patch("mavsdk.System", return_value=system),
        ]

        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            await fly_demo_waypoint_mission.run(arguments)

        artifact_document: dict[str, object] = {}
        if artifact_paths:
            artifact_document = json.loads(artifact_paths[0].read_text(encoding="utf-8"))

        return {
            "director_ctor": director_ctor,
            "director": fake_director,
            "capture_construct": capture_construct,
            "ulog_wait": ulog_wait,
            "hooks": hook_capture,
            "artifact": artifact_document,
        }

    async def test_follow_mode_creates_director_without_capture(self) -> None:
        result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"})
        result["director_ctor"].assert_called_once()
        result["director"].apply_scene.assert_awaited_once()
        result["capture_construct"].assert_not_called()
        result["ulog_wait"].assert_not_called()
        self.assertIsNotNone(result["hooks"].get("on_armed"))
        self.assertNotIn("gz_visual_setup_error", result["artifact"])

    async def test_unset_camera_mode_skips_director(self) -> None:
        result = await self._run_console_mission(None, clear_camera_mode=True)
        result["director_ctor"].assert_not_called()

    async def test_off_camera_mode_skips_director(self) -> None:
        result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "off"})
        result["director_ctor"].assert_not_called()

    async def test_invalid_camera_mode_skips_director(self) -> None:
        result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "bogus"})
        result["director_ctor"].assert_not_called()

    async def test_missing_gz_skips_director(self) -> None:
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"},
            gz_binary=None,
        )
        result["director_ctor"].assert_not_called()

    async def test_director_failure_records_error_and_mission_completes(self) -> None:
        failing_director = MagicMock()
        failing_director.apply_scene = AsyncMock(side_effect=RuntimeError("gz follow unavailable"))
        failing_director.reaffirm_camera_follow_after_arm = MagicMock()
        failing_director.reaffirm_camera_follow_at_rtl = MagicMock()
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "overview"},
            director=failing_director,
        )
        self.assertIn("gz_visual_setup_error", result["artifact"])

    async def test_director_timeout_records_error(self) -> None:
        async def wait_for_stub(coro, timeout):
            if timeout == 35.0:
                raise asyncio.TimeoutError()
            return await coro

        with patch("brain.cli.fly_demo_waypoint_mission.asyncio.wait_for", side_effect=wait_for_stub):
            result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"})
        self.assertIn("gz_visual_setup_error", result["artifact"])


async def _home_iter(home: MagicMock):
    yield home


class _FakeTelemetryRelay:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, stop_event: asyncio.Event) -> None:
        await stop_event.wait()


if __name__ == "__main__":
    unittest.main()