"""Demo Gazebo scene helpers: markers, follow, frame timing (no live gz required)."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from brain.mission.commands import WaypointCommand
from brain.mission.demo_gz_scene import (
    BAYLANDS_DEMO_GROUND_Z_OFFSET_M,
    BAYLANDS_DEMO_SPAWN_WORLD_X_M,
    BAYLANDS_DEMO_SPAWN_WORLD_Y_M,
    CameraFollowEvidence,
    CameraSetupEvidence,
    DemoGzVisualDirector,
    GzCli,
    apply_camera_follow,
    apply_overview_camera,
    DEFAULT_DEMO_OVERVIEW_POSE,
    compute_auto_overview_pose,
    compute_frame_phase_epochs,
    default_demo_gui_config_path,
    effective_demo_gz_camera_mode,
    extract_review_frames,
    gz_gui_rpy_to_quaternion,
    look_direction_alignment,
    mission_overview_target_center,
    mission_overview_world_points,
    overview_orientation_toward,
    parse_follow_offset_env,
    place_waypoint_markers,
    point_in_overview_frustum,
    quaternion_rotates_x_to_forward,
    camera_forward_unit,
    resolve_follow_model_name,
    resolve_overview_pose,
    wait_for_service,
    waypoint_world_position,
)
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop


class WaypointWorldConversionTests(unittest.TestCase):
    def test_waypoint_world_position_uses_spawn_constants(self) -> None:
        waypoint = WaypointCommand(north_m=10.0, east_m=5.0, target_altitude_m=8.0)
        x, y, z = waypoint_world_position(waypoint)
        self.assertEqual(x, BAYLANDS_DEMO_SPAWN_WORLD_X_M + 5.0)
        self.assertEqual(y, BAYLANDS_DEMO_SPAWN_WORLD_Y_M + 10.0)
        self.assertEqual(z, BAYLANDS_DEMO_GROUND_Z_OFFSET_M + 8.0)


class FollowModelValidationTests(unittest.TestCase):
    def test_rejects_injection_characters(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_FOLLOW_MODEL": 'x500"; evil'}, clear=False):
            model, err = resolve_follow_model_name()
            self.assertIsNotNone(err)
            self.assertEqual(model, "x500_0")


class FollowOffsetValidationTests(unittest.TestCase):
    def test_default_offset_when_unset(self) -> None:
        env = dict(os.environ)
        env.pop("BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET", None)
        with unittest.mock.patch.dict(os.environ, env, clear=True):
            offset, err = parse_follow_offset_env()
            self.assertIsNone(err)
            self.assertEqual(offset, (-5.0, -1.5, 2.5))

    def test_rejects_out_of_range(self) -> None:
        with unittest.mock.patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET": "-50 0 2"}, clear=False):
            offset, err = parse_follow_offset_env()
            self.assertIsNone(offset)
            self.assertIsNotNone(err)


class CameraFollowSequencingTests(unittest.TestCase):
    def test_follow_calls_follow_then_offset(self) -> None:
        calls: list[tuple[str, str]] = []

        def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
            if "-l" in argv:
                return MagicMock(stdout="/gui/follow\n/gui/follow/offset\n", returncode=0)
            calls.append((argv[argv.index("-s") + 1], argv[argv.index("--req") + 1]))
            return MagicMock(returncode=0, stdout="", stderr="")

        cli = GzCli(run=fake_run)
        with unittest.mock.patch("brain.mission.demo_gz_scene.gz_binary", return_value="/usr/bin/gz"):
            with unittest.mock.patch("brain.mission.demo_gz_scene.wait_for_service", return_value=True):
                evidence = apply_camera_follow(cli, model_name="x500_0", offset=(-5.0, -1.5, 2.5))
        self.assertTrue(evidence.ok)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0][0], "/gui/follow")
        self.assertIn("x500_0", calls[0][1])
        self.assertEqual(calls[1][0], "/gui/follow/offset")

    def test_no_follow_when_service_missing(self) -> None:
        cli = GzCli(run=MagicMock(return_value=MagicMock(stdout="", returncode=0)))
        with unittest.mock.patch("brain.mission.demo_gz_scene.gz_binary", return_value="/usr/bin/gz"):
            with unittest.mock.patch("brain.mission.demo_gz_scene.wait_for_service", return_value=False):
                evidence = apply_camera_follow(cli, model_name="x500_0", offset=(-5.0, -1.5, 2.5))
        self.assertFalse(evidence.ok)
        self.assertFalse(evidence.requested)


class FrameTimingTests(unittest.TestCase):
    def test_compute_offsets_from_arm_and_progress(self) -> None:
        from datetime import UTC, datetime

        capture = 1_700_000_000.0
        armed = capture + 2.0
        wp2_iso = datetime.fromtimestamp(capture + 70.0, tz=UTC).isoformat().replace("+00:00", "Z")
        rtl_iso = datetime.fromtimestamp(capture + 120.0, tz=UTC).isoformat().replace("+00:00", "Z")
        offsets = compute_frame_phase_epochs(
            capture_started_epoch=capture,
            armed_at_epoch=armed,
            mission_items_reached=[{"index": 2, "timestamp": wp2_iso}],
            execution_events=[{"phase": "returning", "timestamp": rtl_iso}],
        )
        self.assertAlmostEqual(offsets["takeoff"], 7.0)
        self.assertAlmostEqual(offsets["waypoint_2"], 70.0)
        self.assertAlmostEqual(offsets["rtl_start"], 120.0)

    def test_extract_review_frames_invokes_ffmpeg(self) -> None:
        with TemporaryDirectory() as directory:
            video = Path(directory) / "clip.mp4"
            video.write_bytes(b"\x00" * 64)
            calls: list[list[str]] = []

            def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
                calls.append(list(argv))
                output = Path(argv[-1])
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_bytes(b"png")
                return MagicMock(returncode=0)

            with patch("brain.mission.demo_gz_scene.shutil.which", return_value="/usr/bin/ffmpeg"):
                frames = extract_review_frames(
                    video,
                    Path(directory) / "frames",
                    {"takeoff": 5.0},
                    stamp="t1",
                    ffmpeg_run=fake_run,
                )
            self.assertEqual(len(frames), 1)
            self.assertIn("-ss", calls[0])


class DemoGuiConfigTests(unittest.TestCase):
    def test_repo_gui_config_exists(self) -> None:
        path = default_demo_gui_config_path()
        self.assertTrue(path.is_file())
        text = path.read_text(encoding="utf-8")
        self.assertIn("1920", text)
        self.assertIn("199 149 12", text)
        self.assertNotIn("EntityTree", text)


class OverviewCameraTests(unittest.TestCase):
    def test_invalid_camera_mode_falls_back_with_error(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "tracking"}, clear=False):
            mode, err = effective_demo_gz_camera_mode()
        self.assertEqual(mode, "overview")
        self.assertIsNotNone(err)

    def test_verified_default_pose_looks_at_mission_center(self) -> None:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        center = mission_overview_target_center(config)
        pose = DEFAULT_DEMO_OVERVIEW_POSE
        dot = look_direction_alignment(
            (pose.x, pose.y, pose.z),
            center,
            pose.roll,
            pose.pitch,
            pose.yaw,
        )
        self.assertGreater(dot, 0.95)

    def test_overview_orientation_from_verified_position(self) -> None:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        center = mission_overview_target_center(config)
        eye = (DEFAULT_DEMO_OVERVIEW_POSE.x, DEFAULT_DEMO_OVERVIEW_POSE.y, DEFAULT_DEMO_OVERVIEW_POSE.z)
        roll, pitch, yaw = overview_orientation_toward(eye, center)
        self.assertAlmostEqual(roll, 0.0, places=6)
        self.assertAlmostEqual(yaw, 0.785, places=2)
        self.assertAlmostEqual(pitch, 0.5, places=1)

    def test_gz_rpy_quaternion_matches_forward_vector(self) -> None:
        roll, pitch, yaw = 0.0, 0.5, 0.785
        quat = gz_gui_rpy_to_quaternion(roll, pitch, yaw)
        self.assertAlmostEqual(quat[0], -0.09463189003894941, places=6)
        self.assertAlmostEqual(quat[1], 0.22859029822472746, places=6)
        self.assertAlmostEqual(quat[2], 0.3706085142088027, places=6)
        self.assertAlmostEqual(quat[3], 0.8952321543270941, places=6)
        from_quat = quaternion_rotates_x_to_forward(*quat)
        from_rpy = camera_forward_unit(roll, pitch, yaw)
        self.assertAlmostEqual(from_quat[0], from_rpy[0], places=6)
        self.assertAlmostEqual(from_quat[1], from_rpy[1], places=6)
        self.assertAlmostEqual(from_quat[2], from_rpy[2], places=6)

    def test_resolve_overview_uses_verified_default_without_env(self) -> None:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        env = dict(os.environ)
        env.pop("BYTEWOLF_DEMO_GZ_OVERVIEW_POSE", None)
        with patch.dict(os.environ, env, clear=True):
            pose, source, err = resolve_overview_pose(config)
        self.assertIsNone(err)
        self.assertEqual(source, "default")
        self.assertEqual(pose, DEFAULT_DEMO_OVERVIEW_POSE)

    def test_auto_overview_opt_in_fits_frustum(self) -> None:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_OVERVIEW_POSE": "auto"}, clear=False):
            pose, source, _ = resolve_overview_pose(config)
        self.assertEqual(source, "auto")
        eye = (pose.x, pose.y, pose.z)
        points = mission_overview_world_points(config)
        for index, point in enumerate(points):
            self.assertTrue(
                point_in_overview_frustum(
                    point,
                    eye=eye,
                    roll=pose.roll,
                    pitch=pose.pitch,
                    yaw=pose.yaw,
                ),
                msg=f"point {index} {point} outside overview frustum",
            )

    def test_overview_calls_move_to_pose_service(self) -> None:
        calls: list[str] = []

        def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
            if "-i" in argv:
                return MagicMock(
                    stdout="Request: gz.msgs.GUICamera\nReply: gz.msgs.Boolean\n",
                    stderr="",
                    returncode=0,
                )
            if "-l" in argv:
                return MagicMock(stdout="/gui/move_to/pose\n", returncode=0)
            calls.append(argv[argv.index("--req") + 1])
            return MagicMock(returncode=0, stdout="", stderr="")

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        pose = compute_auto_overview_pose(config)
        cli = GzCli(run=fake_run)
        with patch("brain.mission.demo_gz_scene.gz_binary", return_value="/usr/bin/gz"):
            with patch("brain.mission.demo_gz_scene.wait_for_service", return_value=True):
                evidence = apply_overview_camera(cli, pose, pose_source="auto")
        self.assertTrue(evidence.ok)
        self.assertEqual(len(calls), 1)
        self.assertIn("pose:", calls[0])
        self.assertIn("duration: 1", calls[0])


class MarkerPlacementTests(unittest.TestCase):
    def test_markers_best_effort(self) -> None:
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=(WaypointCommand(0, 8, 8.0),),
            max_mission_items=4,
        )
        calls: list[str] = []

        def fake_run(argv, **_kwargs):  # type: ignore[no-untyped-def]
            if "-l" not in argv and "--req" in argv:
                calls.append(argv[argv.index("--req") + 1])
            return MagicMock(returncode=0, stdout="", stderr="")

        cli = GzCli(run=fake_run)
        with unittest.mock.patch("brain.mission.demo_gz_scene.gz_binary", return_value="/usr/bin/gz"):
            evidence = place_waypoint_markers(cli, config)
        self.assertEqual(evidence.requested, 1)
        self.assertEqual(evidence.placed, 1)
        self.assertIn("SPHERE", calls[0])


class VisualDirectorTests(unittest.TestCase):
    def test_overview_mode_does_not_call_follow(self) -> None:
        import asyncio

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        director = DemoGzVisualDirector(config=config)
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "overview"}, clear=False):
            with patch("brain.mission.demo_gz_scene.wait_for_model_spawned", return_value=True):
                with patch("brain.mission.demo_gz_scene.apply_camera_follow") as follow:
                    with patch("brain.mission.demo_gz_scene.apply_overview_camera") as overview:
                        overview.return_value = CameraSetupEvidence(
                            camera_mode="overview",
                            ok=True,
                            pose_source="auto",
                        )
                        asyncio.run(director.after_gui_recording_start())
        follow.assert_not_called()
        overview.assert_called_once()
        self.assertEqual(director.camera_setup_history[-1]["camera_mode"], "overview")

    def test_director_records_offset_validation_error(self) -> None:
        import asyncio

        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=(),
            max_mission_items=2,
        )
        director = DemoGzVisualDirector(config=config)
        env = {"BYTEWOLF_DEMO_GZ_CAMERA_MODE": "follow", "BYTEWOLF_DEMO_GZ_FOLLOW_OFFSET": "bad"}
        with unittest.mock.patch.dict(os.environ, env, clear=False):
            with patch("brain.mission.demo_gz_scene.wait_for_model_spawned", return_value=True):
                asyncio.run(director.after_gui_recording_start())
        self.assertTrue(director.camera_setup_history)
        self.assertIn("error", director.camera_setup_history[0])


if __name__ == "__main__":
    unittest.main()