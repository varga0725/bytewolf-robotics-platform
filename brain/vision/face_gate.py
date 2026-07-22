"""Internal multi-frame P1 face-verification decision gate.

The gate consumes only consent state and scalar similarity evidence supplied by
an adapter. It never accepts or stores raw embeddings/templates, and its output
is the observation-only ``FaceVerification`` contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from .contracts import ResultState
from .face_verification import (
    BiometricConsent,
    ConsentState,
    FaceQuality,
    FaceVerification,
    LivenessResult,
    MatchResult,
    FACE_VERIFICATION_V1,
)


@dataclass(frozen=True)
class FaceVerificationObservation:
    """One private, model-adapter scalar observation with no embedding payload."""

    consent: BiometricConsent
    stream_session_id: str
    frame_sequence: int
    model_id: str
    model_version: str
    threshold_version: str
    quality: FaceQuality
    liveness: LivenessResult
    similarity: float | None
    observed_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.consent, BiometricConsent):
            raise ValueError("Face observation requires BiometricConsent.")
        if not isinstance(self.stream_session_id, str) or not self.stream_session_id.strip() or type(self.frame_sequence) is not int or self.frame_sequence < 0:
            raise ValueError("Face observation requires stream session and non-negative frame sequence.")
        if not all(isinstance(value, str) and value.strip() for value in (self.model_id, self.model_version, self.threshold_version)):
            raise ValueError("Face observation requires model and threshold versions.")
        if not isinstance(self.quality, FaceQuality) or not isinstance(self.liveness, LivenessResult):
            raise ValueError("Face observation requires explicit quality and liveness outcomes.")
        if self.similarity is not None and (not isinstance(self.similarity, (int, float)) or isinstance(self.similarity, bool) or not isfinite(self.similarity) or not 0.0 <= self.similarity <= 1.0):
            raise ValueError("Face observation similarity must be finite and in [0, 1].")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("Face observation timestamp must be timezone-aware.")


@dataclass(frozen=True)
class _GateState:
    last_sequence: int
    candidates: tuple[int, ...]
    cooldown_until: datetime | None


class FaceVerificationGate:
    """Fail-closed confirmation, hysteresis and cooldown state machine."""

    def __init__(
        self,
        *,
        acceptance_threshold: float,
        continuation_threshold: float,
        confirmation_frames: int,
        cooldown: timedelta,
    ) -> None:
        if not _score(acceptance_threshold) or not _score(continuation_threshold) or continuation_threshold > acceptance_threshold:
            raise ValueError("Face gate thresholds must be finite [0, 1] values with continuation <= acceptance.")
        if type(confirmation_frames) is not int or confirmation_frames <= 0:
            raise ValueError("Face gate confirmation_frames must be a positive integer.")
        if not isinstance(cooldown, timedelta) or cooldown < timedelta():
            raise ValueError("Face gate cooldown must be non-negative.")
        self._acceptance = float(acceptance_threshold)
        self._continuation = float(continuation_threshold)
        self._confirmation_frames = confirmation_frames
        self._cooldown = cooldown
        self._states: dict[tuple[str, str], _GateState] = {}

    def observe(self, observation: FaceVerificationObservation) -> FaceVerification:
        """Return one immutable verification evidence record for an input frame."""
        key = (observation.consent.subject_id, observation.stream_session_id)
        state = self._states.get(key)
        consent_state = observation.consent.state(observation.observed_at)
        if state is not None and observation.frame_sequence <= state.last_sequence:
            return self._result(observation, consent_state, (), ResultState.INVALID, "source_invalid", MatchResult.UNAVAILABLE, None)
        baseline = _GateState(observation.frame_sequence, (), None) if state is None else _GateState(observation.frame_sequence, state.candidates, state.cooldown_until)
        self._states[key] = baseline
        if consent_state is not ConsentState.GRANTED:
            self._states[key] = _GateState(observation.frame_sequence, (), baseline.cooldown_until)
            reason = "consent_revoked" if consent_state is ConsentState.REVOKED else "consent_expired"
            return self._result(observation, consent_state, (), ResultState.INVALID, reason, MatchResult.UNAVAILABLE, None)
        if observation.quality is FaceQuality.FAILED:
            self._states[key] = _GateState(observation.frame_sequence, (), baseline.cooldown_until)
            return self._result(observation, consent_state, (), ResultState.INVALID, "quality_failed", MatchResult.UNAVAILABLE, None, liveness=LivenessResult.UNAVAILABLE)
        if observation.liveness is LivenessResult.FAILED:
            self._states[key] = _GateState(observation.frame_sequence, (), baseline.cooldown_until)
            return self._result(observation, consent_state, (), ResultState.INVALID, "liveness_failed", MatchResult.UNAVAILABLE, None)
        if observation.quality is FaceQuality.UNAVAILABLE or observation.liveness is LivenessResult.UNAVAILABLE or observation.similarity is None:
            self._states[key] = _GateState(observation.frame_sequence, (), baseline.cooldown_until)
            return self._result(observation, consent_state, (), ResultState.INVALID, "model_unavailable", MatchResult.UNAVAILABLE, None)
        if baseline.cooldown_until is not None and observation.observed_at < baseline.cooldown_until:
            return self._result(observation, consent_state, baseline.candidates, ResultState.MISSING, "cooldown_active", MatchResult.UNAVAILABLE, None)
        candidates = baseline.candidates
        score = float(observation.similarity)
        if score >= self._acceptance or (candidates and score >= self._continuation):
            candidates = candidates + (observation.frame_sequence,)
            if len(candidates) < self._confirmation_frames:
                self._states[key] = _GateState(observation.frame_sequence, candidates, None)
                return self._result(observation, consent_state, candidates, ResultState.MISSING, "multiframe_pending", MatchResult.UNAVAILABLE, None)
            self._states[key] = _GateState(observation.frame_sequence, (), observation.observed_at + self._cooldown)
            return self._result(observation, consent_state, candidates, ResultState.VALID, "confirmed", MatchResult.MATCHED, score)
        self._states[key] = _GateState(observation.frame_sequence, (), None)
        return self._result(observation, consent_state, (), ResultState.VALID, "not_matched", MatchResult.NOT_MATCHED, score)

    @staticmethod
    def _result(
        observation: FaceVerificationObservation,
        consent_state: ConsentState,
        sequences: tuple[int, ...],
        state: ResultState,
        reason: str,
        match: MatchResult,
        confidence: float | None,
        *,
        liveness: LivenessResult | None = None,
    ) -> FaceVerification:
        return FaceVerification(
            FACE_VERIFICATION_V1, observation.consent.subject_id, observation.consent.consent_record_id,
            consent_state, observation.stream_session_id, sequences or (observation.frame_sequence,),
            observation.model_id, observation.model_version, observation.threshold_version,
            observation.quality, observation.liveness if liveness is None else liveness,
            match, confidence, state, reason, observation.observed_at,
        )


def _score(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and isfinite(value) and 0.0 <= value <= 1.0
