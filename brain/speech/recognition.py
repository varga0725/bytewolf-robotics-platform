"""Turn a recogniser's raw output into the versioned Transcript contract.

This is where the two rules that keep speech honest are applied, in code rather
than in a prompt:

* **An uncertain transcript never suggests anything.** Below the confidence
  threshold the transcript is published without a ``command_suggestion``, so the
  runtime asks back instead of acting on a guess.
* **A partial transcript never suggests anything.** Only a settled utterance can
  propose, because acting on half a sentence can invert its meaning.

What comes out is a document that satisfies ``transcript_v0_1``, which the
loader then re-validates. Building it here and validating it there is deliberate:
the producer cannot talk the consumer into accepting something the schema
forbids.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from brain.speech.adapters.stt import RawTranscription, SttEngine, SttError
from brain.speech.transcript import Transcript, load_transcript

#: Below this the utterance is published, but it proposes nothing. The number is
#: a starting point, not a measured threshold: it wants calibrating against a
#: recorded Hungarian set, like every other quality figure in this stack.
DEFAULT_SUGGESTION_CONFIDENCE = 0.6

DEFAULT_MAX_AGE_S = 30.0
DEFAULT_LANGUAGE = "hu-HU"


class SpeechRecogniser:
    """Recognise one utterance and publish it as a contract-valid transcript."""

    def __init__(
        self,
        engine: SttEngine,
        *,
        source: str = "mic:default",
        language: str = DEFAULT_LANGUAGE,
        max_age_s: float = DEFAULT_MAX_AGE_S,
        suggestion_confidence: float = DEFAULT_SUGGESTION_CONFIDENCE,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._engine = engine
        self._source = source
        self._language = language
        self._max_age_s = max_age_s
        self._suggestion_confidence = suggestion_confidence
        self._now = now

    def recognise(
        self,
        audio: bytes,
        *,
        transcript_id: str,
        session_id: str | None = None,
        audio_ref: str | None = None,
        observed_at: datetime | None = None,
    ) -> Transcript:
        """Transcribe audio into a validated transcript.

        A recogniser failure produces a transcript with ``validity: invalid`` and
        no text -- the turn learns that listening failed, rather than receiving
        silence it might read as "nothing was said".
        """
        spoken_at = observed_at or self._now()
        try:
            raw = self._engine.transcribe(audio, language=self._language)
        except SttError:
            return load_transcript(
                self._document(
                    transcript_id=transcript_id,
                    session_id=session_id,
                    observed_at=spoken_at,
                    validity="invalid",
                    text="",
                    is_final=True,
                    audio_ref=audio_ref,
                )
            )
        return load_transcript(self._document_from(raw, transcript_id, session_id, spoken_at, audio_ref))

    # -- internals --------------------------------------------------------

    def _document_from(
        self,
        raw: RawTranscription,
        transcript_id: str,
        session_id: str | None,
        observed_at: datetime,
        audio_ref: str | None,
    ) -> dict[str, Any]:
        text = raw.text.strip()
        document = self._document(
            transcript_id=transcript_id,
            session_id=session_id,
            observed_at=observed_at,
            validity="valid" if text else "missing",
            text=text,
            is_final=raw.is_final,
            audio_ref=audio_ref,
            confidence=raw.confidence,
            language=raw.language or self._language,
            duration_s=raw.duration_s,
        )
        if self._may_suggest(raw, text):
            document["command_suggestion"] = {
                "intent_text": text,
                "requires_approval": True,
                **({"confidence": raw.confidence} if raw.confidence is not None else {}),
            }
        return document

    def _may_suggest(self, raw: RawTranscription, text: str) -> bool:
        if not text or not raw.is_final:
            return False
        if raw.confidence is None:
            # An engine that reports no confidence is not thereby confident.
            # Publish the words; let the runtime ask what was meant.
            return False
        return raw.confidence >= self._suggestion_confidence

    def _document(
        self,
        *,
        transcript_id: str,
        session_id: str | None,
        observed_at: datetime,
        validity: str,
        text: str,
        is_final: bool,
        audio_ref: str | None,
        confidence: float | None = None,
        language: str | None = None,
        duration_s: float | None = None,
    ) -> dict[str, Any]:
        document: dict[str, Any] = {
            "contract_version": "v0.1",
            "transcript_id": transcript_id,
            "source": self._source,
            "observed_at": observed_at.astimezone(UTC).isoformat(),
            "max_age_s": self._max_age_s,
            "validity": validity,
            "engine": {"id": self._engine.engine_id, "version": self._engine.engine_version},
            "language": language or self._language,
            "text": text,
            "is_final": is_final,
        }
        if session_id is not None:
            document["session_id"] = session_id
        if audio_ref is not None:
            document["audio_ref"] = audio_ref
        if confidence is not None:
            document["confidence"] = confidence
        if duration_s is not None:
            document["duration_s"] = duration_s
        return document
