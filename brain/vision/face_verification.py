"""P1 opt-in, observation-only FaceVerification v1 contracts.

This module contains no detector, embedding, template or control capability.
It records only pseudonymous, consent-bound verification evidence for an audit
or dashboard consumer; no result is an authorization or flight decision.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from math import isfinite
import re

from .contracts import ResultState


FACE_VERIFICATION_V1 = "face_verification.v1"
_PSEUDONYMOUS_SUBJECT = re.compile(r"sub_[0-9a-f]{32,128}\Z")
_CONSENT_RECORD = re.compile(r"consent-[a-z0-9][a-z0-9_-]{7,127}\Z")
_REASON_CODES = frozenset({
    "confirmed", "not_matched", "quality_failed", "liveness_failed",
    "multiframe_pending", "consent_required", "consent_revoked", "consent_expired", "model_unavailable",
    "source_invalid", "threshold_unavailable", "cooldown_active",
})


class FaceVerificationError(ValueError):
    """Face-verification evidence violates the P1 privacy/fail-closed contract."""


class ConsentState(str, Enum):
    GRANTED = "granted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class FaceQuality(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class LivenessResult(str, Enum):
    PASSED = "passed"
    FAILED = "failed"
    UNAVAILABLE = "unavailable"


class MatchResult(str, Enum):
    MATCHED = "matched"
    NOT_MATCHED = "not_matched"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class BiometricConsent:
    """Immutable, pseudonymous opt-in record; revocation remains explicit."""

    subject_id: str
    consent_record_id: str
    granted_at: datetime
    expires_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        _validate_subject_id(self.subject_id)
        _validate_consent_id(self.consent_record_id)
        _aware(self.granted_at, "granted_at")
        if self.expires_at is not None:
            _aware(self.expires_at, "expires_at")
            if self.expires_at <= self.granted_at:
                raise FaceVerificationError("Consent expiry must follow grant time.")
        if self.revoked_at is not None:
            _aware(self.revoked_at, "revoked_at")
            if self.revoked_at < self.granted_at:
                raise FaceVerificationError("Consent revocation cannot precede grant time.")

    def state(self, now: datetime) -> ConsentState:
        _aware(now, "now")
        instant = now.astimezone(UTC)
        if self.revoked_at is not None and self.revoked_at.astimezone(UTC) <= instant:
            return ConsentState.REVOKED
        if self.expires_at is not None and self.expires_at.astimezone(UTC) <= instant:
            return ConsentState.EXPIRED
        return ConsentState.GRANTED

    def allows_verification(self, now: datetime) -> bool:
        return self.state(now) is ConsentState.GRANTED


@dataclass(frozen=True)
class FaceVerification:
    """Versioned biometric verification evidence with no template/embedding data."""

    contract_version: str
    subject_id: str
    consent_record_id: str
    consent_state: ConsentState
    stream_session_id: str
    frame_sequences: tuple[int, ...]
    model_id: str
    model_version: str
    threshold_version: str
    quality: FaceQuality
    liveness: LivenessResult
    match: MatchResult
    confidence: float | None
    state: ResultState
    reason_code: str
    produced_at: datetime

    def __post_init__(self) -> None:
        if self.contract_version != FACE_VERIFICATION_V1:
            raise FaceVerificationError("Unsupported FaceVerification contract version.")
        _validate_subject_id(self.subject_id)
        _validate_consent_id(self.consent_record_id)
        if not isinstance(self.consent_state, ConsentState):
            raise FaceVerificationError("FaceVerification requires an explicit consent state.")
        if not isinstance(self.stream_session_id, str) or not self.stream_session_id.strip():
            raise FaceVerificationError("FaceVerification stream session is required.")
        if not isinstance(self.frame_sequences, tuple) or not self.frame_sequences:
            raise FaceVerificationError("FaceVerification requires immutable source frame sequences.")
        if any(type(sequence) is not int or sequence < 0 for sequence in self.frame_sequences):
            raise FaceVerificationError("FaceVerification frame sequences must be non-negative integers.")
        if tuple(sorted(set(self.frame_sequences))) != self.frame_sequences:
            raise FaceVerificationError("FaceVerification frame sequences must be strictly increasing.")
        if not all(isinstance(value, str) and value.strip() for value in (self.model_id, self.model_version, self.threshold_version)):
            raise FaceVerificationError("FaceVerification requires model and threshold versions.")
        if not isinstance(self.quality, FaceQuality) or not isinstance(self.liveness, LivenessResult) or not isinstance(self.match, MatchResult):
            raise FaceVerificationError("FaceVerification requires explicit quality, liveness and match outcomes.")
        if not isinstance(self.state, ResultState) or self.reason_code not in _REASON_CODES:
            raise FaceVerificationError("FaceVerification requires an explicit state and reason code.")
        _aware(self.produced_at, "produced_at")
        if self.confidence is not None and (not isinstance(self.confidence, (int, float)) or isinstance(self.confidence, bool) or not isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0):
            raise FaceVerificationError("FaceVerification confidence must be a finite number in [0, 1].")
        self._validate_outcome()

    def _validate_outcome(self) -> None:
        if self.consent_state is not ConsentState.GRANTED:
            _require(self.state is ResultState.INVALID, "consent")
            expected = "consent_revoked" if self.consent_state is ConsentState.REVOKED else "consent_expired"
            _require(self.reason_code == expected and self.match is MatchResult.UNAVAILABLE and self.confidence is None, "consent")
        elif self.quality is FaceQuality.FAILED:
            _require(self.state is ResultState.INVALID and self.reason_code == "quality_failed", "quality")
            _require(self.liveness is LivenessResult.UNAVAILABLE and self.match is MatchResult.UNAVAILABLE and self.confidence is None, "quality")
        elif self.liveness is LivenessResult.FAILED:
            _require(self.state is ResultState.INVALID and self.reason_code == "liveness_failed", "liveness")
            _require(self.match is MatchResult.UNAVAILABLE and self.confidence is None, "liveness")
        elif self.match is MatchResult.UNAVAILABLE:
            _require(self.confidence is None, "unavailable match")
            _require(self.state is not ResultState.VALID, "unavailable match")
        elif self.state is ResultState.VALID:
            _require(self.quality is FaceQuality.PASSED and self.liveness is LivenessResult.PASSED and self.confidence is not None, "valid verification")
            expected = "confirmed" if self.match is MatchResult.MATCHED else "not_matched"
            _require(self.reason_code == expected, "valid verification")


def _require(condition: bool, context: str) -> None:
    if not condition:
        raise FaceVerificationError(f"FaceVerification {context} outcome must fail closed.")


def _validate_subject_id(value: str) -> None:
    if not isinstance(value, str) or not _PSEUDONYMOUS_SUBJECT.fullmatch(value):
        raise FaceVerificationError("FaceVerification subject ID must be a pseudonymous sub_<hex> identifier.")


def _validate_consent_id(value: str) -> None:
    if not isinstance(value, str) or not _CONSENT_RECORD.fullmatch(value):
        raise FaceVerificationError("FaceVerification consent record ID is invalid.")


def _aware(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise FaceVerificationError(f"FaceVerification {name} must be timezone-aware.")
