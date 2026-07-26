"""Capture microphone audio for a push-to-talk turn.

Recording goes through ffmpeg's AVFoundation input on macOS, so there is no new
Python audio dependency and the output is already what a recogniser wants: 16 kHz
mono PCM WAV.

The runner is injected, so the whole path is testable on a machine with no
microphone and no permission to use one.

Privacy is the design constraint, not a footnote: this records for a bounded
duration into a temporary file, hands the bytes to the caller, and deletes the
file. Nothing is retained here, and there is no continuous-listening mode to
turn on by accident.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile


DEFAULT_SAMPLE_RATE = 16_000
DEFAULT_DEVICE = "0"
#: Nothing may record without a stated end. An unbounded capture is a hot
#: microphone, which is the thing push-to-talk exists to avoid.
MAX_CAPTURE_S = 30.0


class MicrophoneError(RuntimeError):
    """Audio could not be captured."""


@dataclass
class FfmpegMicrophone:
    """Record a bounded utterance from a local input device."""

    device: str = DEFAULT_DEVICE
    sample_rate: int = DEFAULT_SAMPLE_RATE
    runner: Callable[[list[str], float], subprocess.CompletedProcess] | None = None

    def record(self, seconds: float) -> bytes:
        """Capture ``seconds`` of audio and return the WAV bytes."""
        if seconds <= 0:
            raise MicrophoneError("A capture needs a positive duration.")
        if seconds > MAX_CAPTURE_S:
            raise MicrophoneError(f"A single capture may not exceed {MAX_CAPTURE_S:g} seconds.")
        # Only the default subprocess path needs a local ffmpeg. A caller that
        # injected a runner has supplied its own capture, and refusing here would
        # make this class unusable on a host without ffmpeg for no reason.
        if self.runner is None:
            binary = shutil.which("ffmpeg")
            if binary is None:
                raise MicrophoneError("ffmpeg is required to capture audio on this host.")
        else:
            binary = "ffmpeg"

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "utterance.wav"
            command = [
                binary, "-hide_banner", "-loglevel", "error",
                "-f", "avfoundation", "-i", f":{self.device}",
                "-t", f"{seconds:g}", "-ar", str(self.sample_rate), "-ac", "1",
                "-y", str(destination),
            ]
            run = self.runner or _run
            try:
                completed = run(command, seconds + 10.0)
            except subprocess.TimeoutExpired as error:
                raise MicrophoneError("Audio capture timed out.") from error
            if completed.returncode != 0:
                detail = (completed.stderr or b"").decode("utf-8", errors="replace").strip()
                raise MicrophoneError(
                    f"Audio capture failed: {detail or 'no detail'}. "
                    "On macOS the recording process needs microphone permission."
                )
            try:
                audio = destination.read_bytes()
            except OSError as error:
                raise MicrophoneError("Audio capture produced no file.") from error
        if not audio:
            raise MicrophoneError("Audio capture produced no audio.")
        return audio


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, timeout=timeout, check=False)


def input_devices() -> tuple[tuple[str, str], ...]:
    """Available audio inputs as (index, name), so a wrong device is obvious."""
    binary = shutil.which("ffmpeg")
    if binary is None:
        return ()
    listing = subprocess.run(
        [binary, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""],
        capture_output=True, text=True, timeout=30, check=False,
    )
    devices: list[tuple[str, str]] = []
    in_audio_section = False
    for line in listing.stderr.splitlines():
        if "AVFoundation audio devices" in line:
            in_audio_section = True
            continue
        if "AVFoundation video devices" in line:
            in_audio_section = False
            continue
        if not in_audio_section or "] [" not in line:
            continue
        _prefix, _sep, remainder = line.partition("] [")
        index, _sep, name = remainder.partition("] ")
        if index.strip().isdigit():
            devices.append((index.strip(), name.strip()))
    return tuple(devices)
