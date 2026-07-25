"""Microphone capture for a push-to-talk turn.

The recorder is exercised with an injected runner, so these run on a machine
with no microphone and no permission to use one. What is asserted is the privacy
boundary: every capture is bounded, nothing is retained after the bytes are
handed over, and a failure is a refusal rather than silence that could read as
"nothing was said".
"""

from pathlib import Path
import subprocess
import unittest

from brain.speech.adapters.microphone import (
    MAX_CAPTURE_S,
    FfmpegMicrophone,
    MicrophoneError,
)


class _FakeRunner:
    """Writes WAV bytes where the command says to, like ffmpeg would."""

    def __init__(self, audio: bytes = b"RIFF....WAVEfmt ", returncode: int = 0, stderr: bytes = b""):
        self._audio = audio
        self._returncode = returncode
        self._stderr = stderr
        self.commands: list[list[str]] = []

    def __call__(self, command: list[str], timeout: float) -> subprocess.CompletedProcess:
        self.commands.append(command)
        if self._returncode == 0 and self._audio:
            Path(command[-1]).write_bytes(self._audio)
        return subprocess.CompletedProcess(command, self._returncode, b"", self._stderr)


class MicrophoneTests(unittest.TestCase):
    def test_it_captures_the_requested_audio(self) -> None:
        runner = _FakeRunner(audio=b"RIFF-utterance")
        audio = FfmpegMicrophone(runner=runner).record(3.0)

        self.assertEqual(audio, b"RIFF-utterance")
        command = runner.commands[0]
        self.assertIn("-t", command)
        self.assertEqual(command[command.index("-t") + 1], "3")

    def test_it_asks_for_the_sample_rate_a_recogniser_wants(self) -> None:
        runner = _FakeRunner()
        FfmpegMicrophone(runner=runner).record(1.0)
        command = runner.commands[0]

        self.assertEqual(command[command.index("-ar") + 1], "16000")
        self.assertEqual(command[command.index("-ac") + 1], "1")

    def test_the_recording_is_not_left_on_disk(self) -> None:
        runner = _FakeRunner()
        FfmpegMicrophone(runner=runner).record(1.0)
        destination = Path(runner.commands[0][-1])

        self.assertFalse(destination.exists(), "the captured audio outlived the call")

    def test_an_unbounded_capture_is_refused(self) -> None:
        # A hot microphone is exactly what push-to-talk exists to avoid.
        runner = _FakeRunner()
        with self.assertRaises(MicrophoneError):
            FfmpegMicrophone(runner=runner).record(MAX_CAPTURE_S + 1)
        with self.assertRaises(MicrophoneError):
            FfmpegMicrophone(runner=runner).record(0)
        self.assertEqual(runner.commands, [], "a refused capture still opened the microphone")

    def test_a_failed_capture_is_a_refusal_not_silence(self) -> None:
        runner = _FakeRunner(returncode=1, stderr=b"Input/output error")
        with self.assertRaises(MicrophoneError) as raised:
            FfmpegMicrophone(runner=runner).record(1.0)

        # The message points at the usual cause rather than leaving the operator
        # to guess why a microphone produced nothing.
        self.assertIn("permission", str(raised.exception))

    def test_an_empty_recording_is_a_refusal(self) -> None:
        with self.assertRaises(MicrophoneError):
            FfmpegMicrophone(runner=_FakeRunner(audio=b"")).record(1.0)

    def test_the_speech_domain_never_imports_flight_control(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for source in (root / "brain/speech/adapters/microphone.py", root / "brain/cli/speech_turn.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


class InjectedRunnerTests(unittest.TestCase):
    """A caller that supplied its own capture does not need the host's ffmpeg."""

    def test_an_injected_runner_does_not_require_a_local_ffmpeg(self) -> None:
        import brain.speech.adapters.microphone as microphone_module

        original = microphone_module.shutil.which
        microphone_module.shutil.which = lambda _name: None  # a host with no ffmpeg
        try:
            audio = FfmpegMicrophone(runner=_FakeRunner(audio=b"RIFF-injected")).record(1.0)
        finally:
            microphone_module.shutil.which = original

        self.assertEqual(audio, b"RIFF-injected")

    def test_the_default_path_still_requires_ffmpeg(self) -> None:
        import brain.speech.adapters.microphone as microphone_module

        original = microphone_module.shutil.which
        microphone_module.shutil.which = lambda _name: None
        try:
            with self.assertRaises(MicrophoneError):
                FfmpegMicrophone().record(1.0)
        finally:
            microphone_module.shutil.which = original


if __name__ == "__main__":
    unittest.main()
