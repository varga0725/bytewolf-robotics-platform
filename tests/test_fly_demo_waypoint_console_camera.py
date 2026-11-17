"""Console-spawned demo-waypoint Gazebo camera path (fakes only, no gz/MAVLink)."""

from __future__ import annotations

import argparse
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
from brain.mission import demo_gz_scene
from brain.mission.demo_gz_scene import (
    DemoGzVisualDirector,
    GzCli,
    console_gz_visual_setup_document,
    console_spawned_demo_gz_visual_eligible,
)
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.mission.demo_waypoint_reviewed_plan import write_reviewed_demo_waypoint_plan
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

    def test_demo_gz_visual_kill_switch(self) -> None:
        with patch.dict(
            os.environ,
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow", "BYTEWOLF_DEMO_GZ_VISUAL": "0"},
            clear=False,
        ):
            self.assertFalse(
                console_spawned_demo_gz_visual_eligible(
                    sitl_acceptance=False,
                    deployment_mode="simulation",
                )
            )


class ConsoleGzVisualSetupDocumentTests(unittest.TestCase):
    def test_service_unavailable_records_setup_error(self) -> None:
        import asyncio as aio

        def service_unavailable(*_args, **_kwargs):
            return MagicMock(returncode=1, stdout="", stderr="service not available")

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:1],
            max_mission_items=4,
        )
        cli = GzCli(run=service_unavailable)
        director = DemoGzVisualDirector(config=config, cli=cli)
        with (
            patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"}, clear=False),
            patch.object(demo_gz_scene, "gz_binary", return_value="/usr/bin/gz"),
            patch.object(demo_gz_scene, "wait_for_model_spawned", return_value=True),
            patch.object(demo_gz_scene, "wait_for_service", return_value=False),
        ):
            aio.run(director.apply_scene())
        document = console_gz_visual_setup_document(director)
        self.assertIn("gz_visual_setup", document)
        self.assertIn("gz_visual_setup_error", document)
        self.assertFalse(document["gz_visual_setup"]["ok"])  # type: ignore[index]


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

    async def _run_mission(
        self,
        arguments: argparse.Namespace,
        config: DemoWaypointMissionConfig,
        *,
        gz_binary: str | None = "/usr/bin/gz",
        use_real_director: bool = False,
        director: MagicMock | None = None,
        execute_stub=None,
    ) -> dict[str, object]:
        artifact_dir = Path(arguments.artifact_dir)
        run_id = "console-camera-run"
        adapter = MagicMock()
        adapter._demo_sitl_proof = {"status": "verified"}
        adapter._demo_mission_items_reached = [{"index": 2, "total": 2}]
        adapter._peak_relative_altitude_m = 8.0
        adapter._demo_disarm_reason = None
        adapter.preflight_telemetry = None
        execution = self._completed_execution()

        hook_capture: dict[str, object] = {}

        default_execute = execute_stub

        async def execute_wrapper(_adapter, _config, *, on_armed=None, on_returning=None, **_kwargs):
            hook_capture["on_armed"] = on_armed
            hook_capture["on_returning"] = on_returning
            if default_execute is not None:
                return await default_execute(_adapter, _config, on_armed=on_armed, on_returning=on_returning, **_kwargs)
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
        ulog_wait = AsyncMock(return_value=(None, "missing", None))
        director_ctor = MagicMock()
        if director is not None:
            director_ctor.return_value = director
        elif not use_real_director:
            fake = MagicMock()

            async def _apply_ok() -> None:
                fake.camera_setup_history.append({"camera_mode": "follow", "ok": True})

            fake.apply_scene = _apply_ok
            fake.reaffirm_camera_follow_after_arm = MagicMock()
            fake.reaffirm_camera_follow_at_rtl = MagicMock()
            fake.camera_setup_history = []
            fake.markers = None
            director_ctor.return_value = fake
            director = fake

        system = MagicMock()
        home = MagicMock(latitude_deg=47.0, longitude_deg=8.0, absolute_altitude_m=500.0)
        system.telemetry.home = MagicMock(side_effect=lambda: _home_iter(home))

        patches = [
            patch("brain.cli.fly_demo_waypoint_mission.load_safety_profile", return_value=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)),
            patch("brain.cli.fly_demo_waypoint_mission.file_sha256", return_value="abc"),
            patch("brain.cli.fly_demo_waypoint_mission.authorize_demo_waypoint_mission", return_value=config),
            patch("brain.cli.fly_demo_waypoint_mission.require_simulation_target"),
            patch("brain.cli.fly_demo_waypoint_mission.acquire_px4_link"),
            patch("brain.cli.fly_demo_waypoint_mission.stop_owned_mavsdk_server"),
            patch("brain.cli.fly_demo_waypoint_mission.MavsdkMissionAdapter", return_value=adapter),
            patch.object(adapter, "connect", AsyncMock()),
            patch("brain.cli.fly_demo_waypoint_mission.execute_demo_waypoint_mission", side_effect=execute_wrapper),
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
            patch("mavsdk.System", return_value=system),
        ]
        if not use_real_director:
            patches.append(patch("brain.mission.demo_gz_scene.DemoGzVisualDirector", director_ctor))

        with ExitStack() as stack:
            for item in patches:
                stack.enter_context(item)
            await fly_demo_waypoint_mission.run(arguments)

        artifact_document: dict[str, object] = {}
        if artifact_paths:
            artifact_document = json.loads(artifact_paths[0].read_text(encoding="utf-8"))

        return {
            "director_ctor": director_ctor,
            "director": director,
            "capture_construct": capture_construct,
            "ulog_wait": ulog_wait,
            "hooks": hook_capture,
            "artifact": artifact_document,
        }

    def _console_arguments(self, artifact_dir: Path) -> tuple[argparse.Namespace, DemoWaypointMissionConfig]:
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
        return arguments, config

    async def _run_console_mission(
        self,
        env_overrides: dict[str, str] | None,
        *,
        execute_stub=None,
        **kwargs,
    ) -> dict[str, object]:
        artifact_dir = Path(self.tmp.name) / "artifacts"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        arguments, config = self._console_arguments(artifact_dir)
        merged_env = dict(os.environ)
        merged_env.update({"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"})
        merged_env.pop("BYTEWOLF_SITL_ACCEPTANCE", None)
        if env_overrides is not None:
            merged_env.update(env_overrides)
        with patch.dict(os.environ, merged_env, clear=True):
            return await self._run_mission(arguments, config, execute_stub=execute_stub, **kwargs)

    async def test_follow_mode_creates_director_without_capture(self) -> None:
        result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"})
        result["director_ctor"].assert_called_once()
        result["capture_construct"].assert_not_called()
        result["ulog_wait"].assert_not_called()
        self.assertIsNotNone(result["hooks"].get("on_armed"))
        self.assertIn("gz_visual_setup", result["artifact"])
        self.assertNotIn("gz_visual_setup_error", result["artifact"])

    async def test_unset_camera_mode_skips_director(self) -> None:
        env = dict(os.environ)
        env.pop("BYTEWOLF_DEMO_GZ_CAMERA_MODE", None)
        env.pop("BYTEWOLF_SITL_ACCEPTANCE", None)
        with patch.dict(os.environ, env, clear=True):
            result = await self._run_console_mission(None)
        result["director_ctor"].assert_not_called()

    async def test_off_camera_mode_skips_director(self) -> None:
        result = await self._run_console_mission({"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "off"})
        result["director_ctor"].assert_not_called()

    async def test_demo_gz_visual_zero_skips_director(self) -> None:
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow", "BYTEWOLF_DEMO_GZ_VISUAL": "0"}
        )
        result["director_ctor"].assert_not_called()

    async def test_missing_gz_skips_director(self) -> None:
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"},
            gz_binary=None,
        )
        result["director_ctor"].assert_not_called()

    async def test_director_failure_records_error_and_no_hooks(self) -> None:
        failing = MagicMock()
        failing.camera_setup_history = []
        failing.markers = None

        async def _fail() -> None:
            raise RuntimeError("gz follow unavailable")

        failing.apply_scene = _fail
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "overview"},
            director=failing,
        )
        self.assertIn("gz_visual_setup_error", result["artifact"])
        self.assertIsNone(result["hooks"].get("on_armed"))

    async def test_soft_setup_failure_records_error_and_no_hooks(self) -> None:
        soft_fail = MagicMock()

        async def _soft_fail() -> None:
            soft_fail.camera_setup_history.append(
                {"camera_mode": "follow", "ok": False, "error": "/gui/follow not advertised"}
            )

        soft_fail.camera_setup_history = []
        soft_fail.markers = None
        soft_fail.apply_scene = _soft_fail
        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"},
            director=soft_fail,
        )
        self.assertIn("gz_visual_setup_error", result["artifact"])
        self.assertIn("gz_visual_setup", result["artifact"])
        self.assertIsNone(result["hooks"].get("on_armed"))

    async def test_director_timeout_records_error(self) -> None:
        hang = asyncio.Event()

        async def never_finish_scene(self) -> None:  # type: ignore[no-untyped-def]
            await hang.wait()

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:2],
        )
        artifact_dir = Path(self.tmp.name) / "timeout-artifacts"
        artifact_dir.mkdir(parents=True)
        arguments, _ = self._console_arguments(artifact_dir)
        with (
            patch.dict(
                os.environ,
                {
                    "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
                    "BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow",
                },
                clear=True,
            ),
            patch.object(fly_demo_waypoint_mission, "CONSOLE_DEMO_GZ_VISUAL_SETUP_TIMEOUT_S", 0.05),
            patch.object(DemoGzVisualDirector, "apply_scene", never_finish_scene),
        ):
            result = await self._run_mission(arguments, config, use_real_director=True)
        self.assertIn("gz_visual_setup_error", result["artifact"])
        self.assertIsNone(result["hooks"].get("on_armed"))
        hang.set()

    async def test_reaffirm_task_error_recorded_without_failing_mission(self) -> None:
        flaky = MagicMock()
        flaky.camera_setup_history = []
        flaky.markers = None

        async def _apply_ok() -> None:
            flaky.camera_setup_history.append({"camera_mode": "follow", "ok": True})

        async def _reaffirm_fail() -> None:
            raise RuntimeError("reaffirm broke")

        flaky.apply_scene = _apply_ok
        flaky.reaffirm_camera_follow_after_arm = _reaffirm_fail
        flaky.reaffirm_camera_follow_at_rtl = MagicMock()

        async def _execute_with_hook(_adapter, _config, *, on_armed=None, on_returning=None, **_kwargs):
            if on_armed is not None:
                on_armed()
            await asyncio.sleep(0)
            return self._completed_execution()

        result = await self._run_console_mission(
            {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"},
            director=flaky,
            execute_stub=_execute_with_hook,
        )
        self.assertEqual(result["artifact"].get("outcome"), "completed")
        self.assertIn("gz_visual_followup_errors", result["artifact"])
        self.assertIn("reaffirm broke", str(result["artifact"]["gz_visual_followup_errors"]))

    async def test_reaffirm_task_error_does_not_mask_mission_failure(self) -> None:
        flaky = MagicMock()
        flaky.camera_setup_history = []
        flaky.markers = None

        async def _apply_ok() -> None:
            flaky.camera_setup_history.append({"camera_mode": "follow", "ok": True})

        async def _reaffirm_fail() -> None:
            raise RuntimeError("reaffirm broke")

        flaky.apply_scene = _apply_ok
        flaky.reaffirm_camera_follow_after_arm = _reaffirm_fail
        flaky.reaffirm_camera_follow_at_rtl = MagicMock()

        async def _execute_fail_after_hook(_adapter, _config, *, on_armed=None, on_returning=None, **_kwargs):
            if on_armed is not None:
                on_armed()
            raise RuntimeError("primary mission failure")

        with self.assertRaises(RuntimeError) as raised:
            await self._run_console_mission(
                {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow"},
                director=flaky,
                execute_stub=_execute_fail_after_hook,
            )
        self.assertIn("primary mission failure", str(raised.exception))

    async def test_sitl_acceptance_skip_capture_does_not_use_console_director(self) -> None:
        artifact_dir = Path(self.tmp.name) / "acceptance-artifacts"
        artifact_dir.mkdir(parents=True)
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(altitude_m=8.0, leg_m=5.0)[:2],
        )
        plan_path = artifact_dir / "plan.json"
        write_reviewed_demo_waypoint_plan(plan_path, config, mission_id="regression-plan")
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
        director_ctor = MagicMock()
        with (
            patch.dict(
                os.environ,
                {
                    "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
                    "BYTEWOLF_SITL_ACCEPTANCE": "1",
                    "BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow",
                },
                clear=True,
            ),
            patch(
                "brain.cli.fly_demo_waypoint_mission.load_demo_waypoint_plan_config",
                return_value=config,
            ),
            patch("brain.cli.fly_demo_waypoint_mission.require_matching_demo_waypoint_approval"),
            patch("brain.mission.demo_gz_scene.DemoGzVisualDirector", director_ctor),
        ):
            await self._run_mission(arguments, config)
        director_ctor.assert_not_called()


async def _home_iter(home: MagicMock):
    yield home


class _FakeTelemetryRelay:
    def __init__(self, *args, **kwargs) -> None:
        pass

    async def run(self, stop_event: asyncio.Event) -> None:
        await stop_event.wait()


if __name__ == "__main__":
    unittest.main()