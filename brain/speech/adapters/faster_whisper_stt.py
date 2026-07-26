"""Local Whisper speech recognition through faster-whisper (CTranslate2).

This is the decided STT fallback, and on a machine without a GPU host for the
NIM speech microservice it is the only recogniser that actually runs. It is
fully offline: the model is fetched once and then nothing leaves the machine,
which is the property that matters for a robot that must work without a network.

The model is loaded lazily and can be injected, so importing this module costs
nothing and the tests never download weights.

Confidence: faster-whisper reports per-segment average log-probability, not a
probability. It is converted to a 0-1 figure here so the contract has something
comparable, and the conversion is documented as a heuristic rather than a
calibrated score -- the suggestion threshold above it still wants calibrating
against a recorded Hungarian set.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import exp
from pathlib import Path
import tempfile
from typing import Any

from brain.speech.adapters.stt import RawTranscription, SttError


DEFAULT_MODEL = "large-v3-turbo"
DEFAULT_COMPUTE_TYPE = "int8"


@dataclass
class FasterWhisperStt:
    """Local Whisper via faster-whisper, loaded on first use."""

    model_size: str = DEFAULT_MODEL
    device: str = "cpu"
    compute_type: str = DEFAULT_COMPUTE_TYPE
    model: Any | None = None
    engine_id: str = field(default="")
    engine_version: str = "faster-whisper"

    def __post_init__(self) -> None:
        if not self.engine_id:
            self.engine_id = f"whisper-{self.model_size}"

    def _loaded(self) -> Any:
        if self.model is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as error:  # pragma: no cover - deployment guard
                raise SttError("faster-whisper is not installed on this host.") from error
            self.model = WhisperModel(self.model_size, device=self.device, compute_type=self.compute_type)
        return self.model

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription:
        if not audio:
            raise SttError("No audio was captured.")
        model = self._loaded()
        # faster-whisper reads a path or an array; a temporary file keeps this
        # adapter independent of numpy and of the caller's buffer format.
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as handle:
            handle.write(audio)
            handle.flush()
            try:
                segments, info = model.transcribe(handle.name, language=_base_language(language))
                collected = list(segments)
            except Exception as error:  # noqa: BLE001 - any engine fault is a refusal
                raise SttError(f"Local Whisper failed: {error}") from error

        text = " ".join(segment.text.strip() for segment in collected).strip()
        if not text:
            # Nothing was recognised. That is a 'missing' utterance upstream, not
            # an error: the microphone worked, there were simply no words.
            return RawTranscription(text="", is_final=True, confidence=None,
                                    language=getattr(info, "language", None) or language)
        return RawTranscription(
            text=text,
            is_final=True,
            confidence=_confidence(collected),
            language=getattr(info, "language", None) or language,
            duration_s=getattr(info, "duration", None),
        )


def _confidence(segments: list[Any]) -> float | None:
    """Turn average log-probability into a comparable 0-1 figure.

    A heuristic, not a calibrated probability: it makes segments comparable to
    each other and to a threshold, and nothing more.
    """
    probabilities = [
        exp(segment.avg_logprob)
        for segment in segments
        if getattr(segment, "avg_logprob", None) is not None
    ]
    if not probabilities:
        return None
    return max(0.0, min(1.0, sum(probabilities) / len(probabilities)))


def _base_language(language: str) -> str:
    """Whisper takes a bare language code; 'hu-HU' becomes 'hu'."""
    return language.split("-", 1)[0].lower()
