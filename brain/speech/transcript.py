"""Load and validate the Speech Transcript v0.1 contract, failing closed.

A transcript is an observation of what someone said, not an instruction. This
module is the only place that decides a transcript may be used, and it refuses
anything it cannot fully trust rather than returning a best-effort value.

Freshness is resolved here, not declared by the producer: only the consumer
knows the current time, so an utterance older than its ``max_age_s`` resolves to
``stale``. A spoken request that has aged out must not be acted on -- the
situation it referred to may be gone.

The one thing a transcript may propose is a ``command_suggestion``, and even
that is free text plus a confidence. It carries no structured command, and the
schema pins ``requires_approval`` to true, so speech cannot shorten the
``draft_flight_request`` -> reviewed MissionSpec -> SafetyGate -> approval path.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

import jsonschema


SPEECH_CONTRACT_VERSION = "v0.1"

#: How far ahead of the consumer's clock a producer may sit before the
#: transcript is refused. Small skew between machines is normal; a far-future
#: timestamp is not, and treating it as age zero would keep an utterance
#: actionable long past the moment it was spoken.
MAX_CLOCK_SKEW_S = 2.0

TRANSCRIPT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared/schemas/speech/transcript_v0_1.schema.json"
)


class TranscriptContractError(ValueError):
    """Raised when a document cannot be read as a transcript."""


class TranscriptState(Enum):
    """Whether a transcript may be acted on, and if not, why not."""

    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    STALE = "stale"

    @property
    def usable(self) -> bool:
        return self is TranscriptState.VALID


@dataclass(frozen=True)
class CommandSuggestion:
    """What the speaker appears to have asked for. Never an executable command."""

    intent_text: str
    confidence: float | None

    @property
    def requires_approval(self) -> bool:
        """Always true. Kept as a property so no caller can construct otherwise."""
        return True


@dataclass(frozen=True)
class Transcript:
    """One recognised utterance and everything needed to judge it."""

    transcript_id: str
    source: str
    observed_at: datetime
    max_age_s: float
    declared_validity: str
    engine: dict[str, str]
    language: str
    text: str
    is_final: bool
    confidence: float | None
    language_confidence: float | None
    duration_s: float | None
    audio_ref: str | None
    session_id: str | None
    command_suggestion: CommandSuggestion | None

    def state(self, now: datetime) -> TranscriptState:
        """Resolve usability at a given instant, failing closed."""
        if self.declared_validity == "missing":
            return TranscriptState.MISSING
        if self.declared_validity == "invalid":
            return TranscriptState.INVALID
        age = (_utc(now) - self.observed_at).total_seconds()
        if age < -MAX_CLOCK_SKEW_S:
            return TranscriptState.INVALID
        if age > self.max_age_s:
            return TranscriptState.STALE
        return TranscriptState.VALID


@lru_cache(maxsize=1)
def _validator() -> Any:
    try:
        schema = json.loads(TRANSCRIPT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise TranscriptContractError(
            f"Cannot read the transcript schema '{TRANSCRIPT_SCHEMA_PATH}': {error.strerror}."
        ) from error
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def load_transcript(document: object) -> Transcript:
    """Read a document as a transcript, or refuse it."""
    try:
        _validator().validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise TranscriptContractError(f"Transcript rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."

    suggestion_document = document.get("command_suggestion")
    suggestion = None
    if suggestion_document is not None:
        suggestion = CommandSuggestion(
            intent_text=suggestion_document["intent_text"],
            confidence=suggestion_document.get("confidence"),
        )

    return Transcript(
        transcript_id=document["transcript_id"],
        source=document["source"],
        observed_at=_parse_timestamp(document["observed_at"]),
        max_age_s=_finite_max_age(document["max_age_s"]),
        declared_validity=document["validity"],
        engine=dict(document["engine"]),
        language=document["language"],
        text=document["text"],
        is_final=bool(document["is_final"]),
        confidence=document.get("confidence"),
        language_confidence=document.get("language_confidence"),
        duration_s=document.get("duration_s"),
        audio_ref=document.get("audio_ref"),
        session_id=document.get("session_id"),
        command_suggestion=suggestion,
    )


def to_document(transcript: Transcript) -> dict[str, Any]:
    """Rebuild the contract document from a loaded transcript.

    A saved transcript has to be a transcript: writing a convenient summary
    instead produces a file the loader refuses, which is worse than writing
    nothing at all. What comes out of here loads straight back in.
    """
    document: dict[str, Any] = {
        "contract_version": SPEECH_CONTRACT_VERSION,
        "transcript_id": transcript.transcript_id,
        "source": transcript.source,
        "observed_at": transcript.observed_at.isoformat(),
        "max_age_s": transcript.max_age_s,
        "validity": transcript.declared_validity,
        "engine": dict(transcript.engine),
        "language": transcript.language,
        "text": transcript.text,
        "is_final": transcript.is_final,
    }
    for key, value in (
        ("session_id", transcript.session_id),
        ("audio_ref", transcript.audio_ref),
        ("confidence", transcript.confidence),
        ("language_confidence", transcript.language_confidence),
        ("duration_s", transcript.duration_s),
    ):
        if value is not None:
            document[key] = value
    if transcript.command_suggestion is not None:
        suggestion: dict[str, Any] = {
            "intent_text": transcript.command_suggestion.intent_text,
            "requires_approval": True,
        }
        if transcript.command_suggestion.confidence is not None:
            suggestion["confidence"] = transcript.command_suggestion.confidence
        document["command_suggestion"] = suggestion
    return document


def _finite_max_age(value: object) -> float:
    """Refuse a freshness window that cannot expire.

    JSON has no infinity literal, but an oversized exponent such as ``1e400``
    parses to ``inf`` and satisfies a lower-bound-only schema check. An infinite
    window would keep a spoken request actionable forever.
    """
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise TranscriptContractError("max_age_s must be a positive, finite number of seconds.")
    return number


def _parse_timestamp(value: str) -> datetime:
    if "T" not in value and "t" not in value:
        raise TranscriptContractError(
            f"Transcript timestamp '{value}' is not RFC 3339: date and time must be joined by 'T'."
        )
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise TranscriptContractError(f"Transcript timestamp '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise TranscriptContractError(
            f"Transcript timestamp '{value}' has no offset; an age cannot be measured from it."
        )
    return timestamp.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise TranscriptContractError("The current time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
