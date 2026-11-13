"""Screen capture mode selection and graceful skips."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, patch

from brain.mission.demo_visual_capture import optional_screen_capture, resolve_screen_capture_mode


class ScreenCaptureModeResolutionTests(unittest.TestCase):
    def test_cli_overrides_env(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE": "xvfb"}, clear=False):
            self.assertEqual(resolve_screen_capture_mode("x11"), "x11")

    def test_env_selects_x11(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_DEMO_SCREEN_CAPTURE_MODE": "x11"}, clear=False):
            self.assertEqual(resolve_screen_capture_mode(None), "x11")

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
            with patch("brain.mission.demo_visual_capture.shutil.which", return_value=None):
                self.assertIsNone(optional_screen_capture(output, mode="xvfb"))

    def test_x11_without_display_skips(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("DISPLAY", None)
                with patch("brain.mission.demo_visual_capture.shutil.which") as which:
                    which.side_effect = lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None
                    self.assertIsNone(optional_screen_capture(output, mode="x11"))

    def test_x11_without_region_skips_full_desktop(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
                os.environ.pop("BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY", None)
                with patch("brain.mission.demo_visual_capture.shutil.which") as which:
                    which.return_value = "/usr/bin/ffmpeg"
                    with patch(
                        "brain.mission.demo_visual_capture._locate_window_geometry",
                        return_value=None,
                    ):
                        self.assertIsNone(optional_screen_capture(output, mode="x11"))

    def test_xvfb_missing_xvfb_run_skips(self) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"

            def fake_which(name: str) -> str | None:
                return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

            with patch("brain.mission.demo_visual_capture.shutil.which", side_effect=fake_which):
                self.assertIsNone(optional_screen_capture(output, mode="xvfb"))

    @patch("brain.mission.demo_visual_capture.subprocess.run")
    def test_x11_geometry_env_invokes_ffmpeg(self, run_mock: MagicMock) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            output.write_bytes(b"fake")
            run_mock.return_value = MagicMock(returncode=0)

            def fake_which(name: str) -> str | None:
                return "/usr/bin/ffmpeg" if name == "ffmpeg" else None

            with patch.dict(
                os.environ,
                {
                    "DISPLAY": ":1",
                    "BYTEWOLF_DEMO_SCREEN_CAPTURE_GEOMETRY": "640x480+10+20",
                },
                clear=False,
            ):
                with patch("brain.mission.demo_visual_capture.shutil.which", side_effect=fake_which):
                    result = optional_screen_capture(output, mode="x11", duration_s=1.0)
            self.assertEqual(result, output)
            command = run_mock.call_args.args[0]
            self.assertIn("x11grab", command)
            self.assertIn(":1+10,20", command)
            self.assertIn("640x480", command)

    @patch("brain.mission.demo_visual_capture.subprocess.run")
    def test_xvfb_mode_invokes_xvfb_run(self, run_mock: MagicMock) -> None:
        with TemporaryDirectory() as directory:
            output = Path(directory) / "clip.mp4"
            output.write_bytes(b"fake")
            run_mock.return_value = MagicMock(returncode=0)

            def fake_which(name: str) -> str | None:
                if name in {"ffmpeg", "xvfb-run"}:
                    return f"/usr/bin/{name}"
                return None

            with patch("brain.mission.demo_visual_capture.shutil.which", side_effect=fake_which):
                result = optional_screen_capture(output, mode="xvfb", duration_s=1.0)
            self.assertEqual(result, output)
            command = run_mock.call_args.args[0]
            self.assertEqual(command[0], "/usr/bin/xvfb-run")
            self.assertIn("/usr/bin/ffmpeg", command)


if __name__ == "__main__":
    unittest.main()
