"""macOS system speech synthesis — the dev machine's working Hungarian voice.

The decided primary TTS is XTTS-v2, which needs a GPU host. This adapter exists
so the speech path is exercisable on the development Mac today: macOS ships a
Hungarian voice (Tünde), it runs offline, and it costs nothing to install.

It is deliberately not a candidate for the robot: it only exists on macOS, so it
cannot follow the stack onto a Jetson. Treat it as the dev-box voice and the
generator of Hungarian test audio, not as a production decision.

The text is passed as an argument vector, never through a shell, so an utterance
containing shell metacharacters is spoken rather than executed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import subprocess
import tempfile

from brain.speech.adapters.tts import SpokenAudio, TtsError


DEFAULT_HUNGARIAN_VOICE = "Tünde"
#: 16 kHz signed little-endian PCM: what speech recognisers expect, and what
#: makes this adapter double as a generator of test audio for the STT side.
DEFAULT_DATA_FORMAT = "LEI16@16000"


@dataclass
class MacSayTts:
    """Synthesise speech with the macOS ``say`` command."""

    voice: str = DEFAULT_HUNGARIAN_VOICE
    data_format: str = DEFAULT_DATA_FORMAT
    timeout_s: float = 30.0
    engine_id: str = "macos-say"
    engine_version: str = "system"

    def speak(self, text: str, *, language: str) -> SpokenAudio:
        spoken = text.strip()
        if not spoken:
            raise TtsError("There is nothing to say.")
        binary = shutil.which("say")
        if binary is None:
            raise TtsError("The macOS 'say' command is not available on this host.")
        # `say` does not fail on an unknown voice: it quietly substitutes the
        # system default, which would read Hungarian text in an English voice and
        # sound like a bug nobody can trace. Check first and refuse instead.
        if self.voice not in installed_voices():
            raise TtsError(
                f"The macOS voice {self.voice!r} is not installed; "
                "install it in System Settings > Accessibility > Spoken Content."
            )
        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "utterance.wav"
            try:
                subprocess.run(
                    [binary, "-v", self.voice, "-o", str(destination),
                     "--data-format", self.data_format, spoken],
                    capture_output=True, timeout=self.timeout_s, check=True,
                )
            except subprocess.TimeoutExpired as error:
                raise TtsError("macOS speech synthesis timed out.") from error
            except subprocess.CalledProcessError as error:
                detail = (error.stderr or b"").decode("utf-8", errors="replace").strip()
                raise TtsError(f"macOS speech synthesis failed: {detail or 'no detail'}") from error
            try:
                audio = destination.read_bytes()
            except OSError as error:
                raise TtsError("macOS speech synthesis produced no file.") from error
        if not audio:
            raise TtsError("macOS speech synthesis produced no audio.")
        return SpokenAudio(audio, self.engine_id, self.engine_version, language, spoken)


def _voice_listing() -> tuple[tuple[str, str], ...]:
    """Every installed voice as (name, locale)."""
    binary = shutil.which("say")
    if binary is None:
        return ()
    try:
        listing = subprocess.run([binary, "-v", "?"], capture_output=True, text=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        return ()
    voices = []
    for line in listing.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            voices.append((parts[0], parts[1]))
    return tuple(voices)


def installed_voices() -> frozenset[str]:
    """Names of every installed voice."""
    return frozenset(name for name, _locale in _voice_listing())


def available_voices(language_prefix: str = "hu") -> tuple[str, ...]:
    """Voices installed for a language, so a missing one is a clear failure."""
    return tuple(
        name for name, locale in _voice_listing()
        if locale.lower().startswith(language_prefix.lower())
    )
