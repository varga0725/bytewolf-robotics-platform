"""Text-to-speech adapters behind one port.

The decided primary is Coqui XTTS-v2, running locally, with pre-generated fixed
Hungarian messages as the fallback for critical states. Both sit behind
``TtsEngine`` so the choice can be re-made -- which matters here, because
XTTS-v2's weights ship under a non-commercial licence and that question is still
open for a commercial release.

The fallback is not a lesser copy of the primary: it exists so the robot can
still say the few things that matter when synthesis is unavailable. A drone that
cannot speak should still be able to say that it cannot speak.

Voice cloning is deliberately not exposed here. It is permitted only with
documented consent and an authorised sample, which is an enrolment workflow, not
a synthesis parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


class TtsError(RuntimeError):
    """Speech could not be synthesised."""


@dataclass(frozen=True)
class SpokenAudio:
    """Synthesised audio and what produced it."""

    audio: bytes
    engine_id: str
    engine_version: str
    language: str
    text: str


class TtsEngine(Protocol):
    """The slice of a synthesiser the speech pipeline depends on."""

    engine_id: str
    engine_version: str

    def speak(self, text: str, *, language: str) -> SpokenAudio: ...


@dataclass
class XttsTts:
    """Coqui XTTS-v2, running locally, with the model injected.

    The synthesiser object is supplied by the caller so this module stays
    importable on a machine with no model. ``speaker_wav`` is accepted only
    because XTTS needs a reference voice; supplying one is an assertion that the
    sample is owned or authorised in writing.
    """

    synthesiser: Any
    speaker_wav: Path | None = None
    engine_id: str = "coqui/xtts-v2"
    engine_version: str = "v2"

    def speak(self, text: str, *, language: str) -> SpokenAudio:
        if not text.strip():
            raise TtsError("There is nothing to say.")
        try:
            audio = self.synthesiser.tts(
                text=text,
                language=_base_language(language),
                **({"speaker_wav": str(self.speaker_wav)} if self.speaker_wav else {}),
            )
        except Exception as error:  # noqa: BLE001 - any engine fault is a refusal
            raise TtsError(f"XTTS synthesis failed: {error}") from error
        payload = bytes(audio) if not isinstance(audio, bytes) else audio
        if not payload:
            raise TtsError("XTTS returned no audio.")
        return SpokenAudio(payload, self.engine_id, self.engine_version, language, text)


@dataclass
class FixedMessageTts:
    """Pre-generated Hungarian messages for critical states.

    The fallback when synthesis is unavailable. It speaks only what was recorded
    in advance: an unknown message is refused rather than approximated, because
    a robot improvising a safety message is worse than a silent one.
    """

    clips: dict[str, bytes] = field(default_factory=dict)
    engine_id: str = "fixed-messages"
    engine_version: str = "v1"

    def speak(self, text: str, *, language: str) -> SpokenAudio:
        clip = self.clips.get(text.strip())
        if clip is None:
            raise TtsError("No pre-generated clip matches that message.")
        return SpokenAudio(clip, self.engine_id, self.engine_version, language, text)


@dataclass
class FallbackTts:
    """Try each synthesiser in order; the first audio wins."""

    engines: tuple[TtsEngine, ...]

    def __post_init__(self) -> None:
        if not self.engines:
            raise TtsError("A fallback chain needs at least one synthesiser.")
        self.engine_id = self.engines[0].engine_id
        self.engine_version = self.engines[0].engine_version
        self.served_by: str | None = None

    def speak(self, text: str, *, language: str) -> SpokenAudio:
        failures: list[str] = []
        for engine in self.engines:
            try:
                spoken = engine.speak(text, language=language)
            except TtsError as error:
                failures.append(f"{engine.engine_id}: {error}")
                continue
            self.served_by = spoken.engine_id
            return spoken
        raise TtsError("; ".join(failures) or "no synthesiser available")


def _base_language(language: str) -> str:
    """XTTS takes a bare language code; 'hu-HU' becomes 'hu'."""
    return language.split("-", 1)[0].lower()
