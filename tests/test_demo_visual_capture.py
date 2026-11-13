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


if __name__ == "__main__":
    unittest.main()
