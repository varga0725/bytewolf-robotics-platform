"""Screen capture mode selection and graceful skips."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from brain.mission.demo_visual_capture import (
    DemoFlightScreenCapture,
    optional_screen_capture,
    resolve_screen_capture_mode,
)


class ScreenCaptureModeResolutionTests(unittest.TestCase):
    def test_cli_overrides_env(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE": "xvfb"}, clear=False):
            self.assertEqual(resolve_screen_capture_mode("x11"), "x11")

    def test_invalid_env_returns_none(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE": "desktop"}, clear=False):
            self.assertIsNone(resolve_screen_capture_mode(None))

    def test_default_is_xvfb_when_unset(self) -> None:
        env = dict(os.environ)
        env.pop("BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(resolve_screen_capture_mode(None), "xvfb")


class OptionalScreenCaptureTests(unittest.TestCase):
    def test_missing_ffmpeg_skips(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            capture = DemoFlightScreenCapture(output, mode="xvfb")
            result = capture.start()
            self.assertEqual(result.status, "skipped")

    @patch("brain.mission.demo_visual_capture.DemoFlightScreenCapture.stop")
    @patch("brain.mission.demo_visual_capture.DemoFlightScreenCapture.start")
    def test_legacy_helper_uses_flight_capture(self, start_mock: MagicMock, stop_mock: MagicMock) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            start_mock.return_value.status = "recording"
            stop_mock.return_value.status = "captured"
            stop_mock.return_value.relative_path = "clip.mp4"
            with patch("brain.mission.demo_visual_capture.shutil.which", return_value="/usr/bin/ffmpeg"):
                self.assertEqual(optional_screen_capture(output, mode="x11", duration_s=0.1), output)


class WindowCleanupSafetyTests(unittest.TestCase):
    def test_cleanup_never_uses_xdotool_windowclose(self) -> None:
        from brain.mission.demo_visual_capture import _CaptureSession

        session = _CaptureSession()
        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.return_value = 0
        session.child_processes.append(proc)
        with patch("brain.mission.demo_visual_capture.shutil.which", return_value="/usr/bin/xdotool"):
            with patch("brain.mission.demo_visual_capture.subprocess.run") as run_mock:
                session.cleanup()
                for call in run_mock.call_args_list:
                    self.assertNotIn("windowclose", call.args[0])

    def test_xvfb_uses_default_geometry_without_xdotool(self) -> None:
        from brain.mission.demo_visual_capture import DemoFlightScreenCapture

        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            with patch("brain.mission.demo_visual_capture.shutil.which") as which:
                which.side_effect = lambda name: f"/usr/bin/{name}" if name in {"ffmpeg", "Xvfb", "gz"} else None
                with patch("brain.mission.demo_visual_capture.subprocess.Popen") as popen:
                    popen.side_effect = lambda *args, **kwargs: MagicMock(pid=4242, poll=MagicMock(return_value=None))
                    with patch("brain.mission.demo_visual_capture._wait_s"):
                        capture = DemoFlightScreenCapture(output, mode="xvfb")
                        result = capture.start()
            self.assertEqual(result.status, "recording")

    def test_stop_marks_failed_when_gz_gui_not_alive(self) -> None:
        from brain.mission.demo_visual_capture import DemoFlightScreenCapture, _CaptureSession

        with TemporaryDirectory() as directory:
            output = Path(directory) / "x.mp4"
            output.write_bytes(b"video")
            capture = DemoFlightScreenCapture(output, mode="xvfb")
            gui = MagicMock(pid=4242, poll=MagicMock(return_value=1))
            capture._session = _CaptureSession(
                launched_gz_gui=True,
                launched_gz_pid=4242,
                launched_gz_process=gui,
                gui_liveness_samples=[{"elapsed_s": 1.0, "alive": True}],
            )
            capture._ffmpeg = MagicMock(poll=MagicMock(return_value=0))
            capture._started_at = __import__("time").monotonic()
            result = capture.stop()
            self.assertEqual(result.status, "failed")
            self.assertFalse(result.gz_gui_alive_at_stop)

    def test_xvfb_without_gz_gui_uses_captured_no_gui(self) -> None:
        from brain.mission.demo_visual_capture import DemoFlightScreenCapture, _CaptureSession

        with TemporaryDirectory() as directory:
            output = Path(directory) / "x.mp4"
            output.write_bytes(b"video")
            capture = DemoFlightScreenCapture(output, mode="xvfb")
            capture._session = _CaptureSession()
            capture._ffmpeg = MagicMock(poll=MagicMock(return_value=0))
            capture._started_at = __import__("time").monotonic()
            with patch.dict(os.environ, {"BYTEWOLF_DEMO_LAUNCH_GZ_GUI": "0"}, clear=False):
                result = capture.stop()
            self.assertEqual(result.status, "captured_no_gui")
            self.assertIsNotNone(result.relative_path)

    def test_child_process_is_alive_ignores_zombies(self) -> None:
        from brain.mission.demo_visual_capture import _child_process_is_alive

        zombie = MagicMock(poll=MagicMock(return_value=0))
        self.assertFalse(_child_process_is_alive(zombie))
        running = MagicMock(poll=MagicMock(return_value=None))
        self.assertTrue(_child_process_is_alive(running))

    def test_gz_gui_refuses_non_loopback_gz_ip(self) -> None:
        from brain.mission.demo_visual_capture import _launch_gz_gui_client

        with patch.dict(os.environ, {"GZ_IP": "192.0.2.1"}, clear=False):
            with patch("brain.mission.demo_visual_capture.shutil.which", return_value="/usr/bin/gz"):
                self.assertIsNone(_launch_gz_gui_client(":199", attach_to_running_server=True))

    def test_gz_gui_extra_args_allowlist_accepts_render_engine(self) -> None:
        from brain.mission.demo_visual_capture import _validated_gz_gui_args

        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_GUI_ARGS": "--render-engine ogre2"}, clear=False):
            allowed, rejected = _validated_gz_gui_args()
            self.assertEqual(allowed, ["--render-engine", "ogre2"])
            self.assertEqual(rejected, [])

    def test_gz_gui_extra_args_rejects_unknown_tokens(self) -> None:
        from brain.mission.demo_visual_capture import _validated_gz_gui_args

        with patch.dict(os.environ, {"BYTEWOLF_DEMO_GZ_GUI_ARGS": "--foo bar"}, clear=False):
            allowed, rejected = _validated_gz_gui_args()
            self.assertEqual(allowed, [])
            self.assertEqual(rejected, ["--foo", "bar"])

    def test_gz_gui_extra_env_rejects_ld_preload_gz_ip_and_path(self) -> None:
        from brain.mission.demo_visual_capture import _CaptureSession, _gz_transport_env, _parse_gz_gui_extra_env

        with patch.dict(
            os.environ,
            {
                "BYTEWOLF_DEMO_GZ_GUI_EXTRA_ENV": (
                    '{"LD_PRELOAD":"/evil.so","GZ_IP":"192.0.2.2","PATH":"/tmp","LIBGL_ALWAYS_SOFTWARE":"1"}'
                )
            },
            clear=False,
        ):
            pairs, rejected = _parse_gz_gui_extra_env()
            self.assertEqual(pairs, {"LIBGL_ALWAYS_SOFTWARE": "1"})
            self.assertEqual(sorted(rejected), ["GZ_IP", "LD_PRELOAD", "PATH"])
            session = _CaptureSession()
            env = _gz_transport_env(":199", attach_to_running_server=True, session=session)
            assert env is not None
            self.assertEqual(env["GZ_IP"], "127.0.0.1")
            self.assertEqual(env["DISPLAY"], ":199")
            self.assertEqual(env.get("LD_PRELOAD"), os.environ.get("LD_PRELOAD"))
            self.assertEqual(sorted(session.gz_gui_extra_env_rejected), ["GZ_IP", "LD_PRELOAD", "PATH"])

    def test_png_uniform_black_frame_is_rejected(self) -> None:
        from brain.mission.demo_visual_capture import _png_has_non_uniform_content

        try:
            from PIL import Image
        except ImportError:
            self.skipTest("Pillow not installed")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "black.png"
            Image.new("RGB", (64, 64), color=(0, 0, 0)).save(path)
            self.assertFalse(_png_has_non_uniform_content(path))

    def test_screenshot_skipped_when_gz_gui_not_alive(self) -> None:
        from brain.mission.demo_visual_capture import DemoFlightScreenCapture, _CaptureSession

        with TemporaryDirectory() as directory:
            shot = Path(directory) / "mid.png"
            capture = DemoFlightScreenCapture(Path(directory) / "clip.mp4", mode="xvfb")
            gui = MagicMock(poll=MagicMock(return_value=1))
            capture._session = _CaptureSession(
                screenshot_path=shot,
                launched_gz_process=gui,
                grab_region=(640, 480, 0, 0),
                grab_display=":199",
            )
            capture._capture_screenshot_frame()
            self.assertFalse(capture._session.screenshot_gui_alive)
            self.assertIsNone(capture._session.screenshot_relative_path)

    def test_launch_gz_gui_defaults_x11_off_xvfb_on(self) -> None:
        from brain.mission.demo_visual_capture import _should_launch_gz_gui

        env = dict(os.environ)
        env.pop("BYTEWOLF_DEMO_LAUNCH_GZ_GUI", None)
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(_should_launch_gz_gui("x11"))
            self.assertTrue(_should_launch_gz_gui("xvfb"))


if __name__ == "__main__":
    unittest.main()
