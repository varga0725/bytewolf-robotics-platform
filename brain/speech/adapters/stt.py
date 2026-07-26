"""Speech-to-text adapters behind one port.

The decided primary is NVIDIA Parakeet TDT 0.6B v3 through the NIM speech
microservice, with local Whisper as the offline fallback. Both sit behind
``SttEngine`` so the choice can be re-made against a recorded Hungarian audio set
without touching anything above.

Every adapter fails closed: a transport error, an HTTP error or an unreadable
response raises ``SttError``. It never returns an empty or invented transcript,
because a fabricated utterance is indistinguishable from a real one once it
reaches the runtime.

Nothing here can reach a flight path. What comes out is text; whether it becomes
a suggestion at all is decided by ``brain.speech.recognition``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import httpx


DEFAULT_NIM_SPEECH_URL = "https://integrate.api.nvidia.com/v1"
PARAKEET_MODEL = "nvidia/parakeet-tdt-0.6b-v3"


class SttError(RuntimeError):
    """The recogniser could not produce a transcript it stands behind."""


@dataclass(frozen=True)
class RawTranscription:
    """What an engine returned, before the contract is built around it."""

    text: str
    is_final: bool = True
    confidence: float | None = None
    language: str | None = None
    duration_s: float | None = None


class SttEngine(Protocol):
    """The slice of a recogniser the speech pipeline depends on."""

    engine_id: str
    engine_version: str

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription: ...


@dataclass
class ParakeetNimStt:
    """NVIDIA Parakeet through the NIM speech microservice.

    Uses the OpenAI-compatible transcriptions route
    (``POST {base_url}/audio/transcriptions``, multipart with ``file``,
    ``model`` and ``language``), so the platform's existing NVIDIA key is the
    only credential involved.
    """

    api_key: str
    model: str = PARAKEET_MODEL
    base_url: str = DEFAULT_NIM_SPEECH_URL
    engine_id: str = PARAKEET_MODEL
    engine_version: str = "v3"
    timeout_s: float = 15.0
    client: httpx.Client | None = None

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription:
        if not audio:
            raise SttError("No audio was captured.")
        owned = self.client is None
        http = self.client or httpx.Client(timeout=self.timeout_s)
        try:
            response = http.post(
                f"{self.base_url.rstrip('/')}/audio/transcriptions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                files={"file": ("utterance.wav", audio, "audio/wav")},
                data={"model": self.model, "language": language},
            )
        except httpx.HTTPError as error:
            raise SttError(f"NIM speech request failed: {error}") from error
        finally:
            if owned:
                http.close()
        if response.status_code >= 400:
            raise SttError(f"NIM speech returned HTTP {response.status_code}.")
        return _parse_openai_transcription(response, language)

    @classmethod
    def from_env(cls, env: dict[str, str], client: httpx.Client | None = None) -> ParakeetNimStt:
        """Build from the environment, refusing without a key."""
        api_key = env.get("NVIDIA_API_KEY", "").strip()
        if not api_key:
            raise SttError("NVIDIA_API_KEY must be set to use the NIM speech recogniser.")
        return cls(
            api_key=api_key,
            model=env.get("NIM_STT_MODEL", "").strip() or PARAKEET_MODEL,
            base_url=env.get("NIM_SPEECH_URL", "").strip() or DEFAULT_NIM_SPEECH_URL,
            client=client,
        )


def _parse_openai_transcription(response: httpx.Response, requested_language: str) -> RawTranscription:
    try:
        document = response.json()
    except ValueError as error:
        raise SttError("NIM speech returned a body that is not JSON.") from error
    if not isinstance(document, dict):
        raise SttError("NIM speech returned a body that is not an object.")
    text = document.get("text")
    if not isinstance(text, str):
        raise SttError("NIM speech returned no transcript text.")
    return RawTranscription(
        text=text.strip(),
        is_final=True,
        confidence=_optional_number(document.get("confidence")),
        language=document.get("language") if isinstance(document.get("language"), str) else requested_language,
        duration_s=_optional_number(document.get("duration")),
    )


@dataclass
class LocalWhisperStt:
    """The offline fallback: a local Whisper model, injected rather than loaded.

    The model object is supplied by the caller (faster-whisper, whisper.cpp
    bindings, whatever the host provides) so this module stays importable, and
    testable, on a machine with no model at all.
    """

    model: Any
    engine_id: str = "whisper-large-v3-turbo"
    engine_version: str = "local"

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription:
        if not audio:
            raise SttError("No audio was captured.")
        try:
            result = self.model.transcribe(audio, language=language)
        except Exception as error:  # noqa: BLE001 - any engine fault is a refusal
            raise SttError(f"Local recogniser failed: {error}") from error
        text = result.get("text") if isinstance(result, dict) else None
        if not isinstance(text, str):
            raise SttError("Local recogniser returned no transcript text.")
        return RawTranscription(
            text=text.strip(),
            is_final=True,
            confidence=_optional_number(result.get("confidence")) if isinstance(result, dict) else None,
            language=result.get("language") if isinstance(result, dict) else language,
        )


@dataclass
class FallbackStt:
    """Try each recogniser in order; the first transcript wins.

    A recogniser that fails is skipped, not retried forever, and if every one
    fails the error names them all -- silence about which engine broke is how a
    speech stack becomes unmaintainable.
    """

    engines: tuple[SttEngine, ...]

    def __post_init__(self) -> None:
        if not self.engines:
            raise SttError("A fallback chain needs at least one recogniser.")
        self.engine_id = self.engines[0].engine_id
        self.engine_version = self.engines[0].engine_version
        self.served_by: str | None = None

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription:
        failures: list[str] = []
        for engine in self.engines:
            try:
                transcription = engine.transcribe(audio, language=language)
            except SttError as error:
                failures.append(f"{engine.engine_id}: {error}")
                continue
            self.served_by = engine.engine_id
            self.engine_id = engine.engine_id
            self.engine_version = engine.engine_version
            return transcription
        raise SttError("; ".join(failures) or "no recogniser available")


def _optional_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)
