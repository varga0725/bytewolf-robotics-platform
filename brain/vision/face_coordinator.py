"""Private P1 composition of face detection, quality, liveness and 1:1 match.

The coordinator accepts only a prevalidated fresh frame and returns only the
immutable observation-only ``FaceVerification`` record. Pixels, landmarks,
embeddings, templates and raw cosine scores remain local to this call.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Protocol

from .contracts import FrameValidation, ResultState
from .face_alignment import ScrfdFaceCandidate
from .face_embedding import PrivateFaceEmbedding, PrivateOneToOneVerifier
from .face_gate import FaceVerificationGate, FaceVerificationObservation
from .face_quality import FaceQualityGate, FaceQualityMetrics
from .face_verification import BiometricConsent, ConsentState, FaceQuality, FaceVerification, LivenessResult


class _Detector(Protocol):
    def detect_single_bgr(self, image: object) -> ScrfdFaceCandidate | None: ...


class _Embedder(Protocol):
    model_id: str
    model_version: str

    def embed_aligned_bgr(self, image: object) -> PrivateFaceEmbedding: ...


class FaceVerificationCoordinator:
    """Fail-closed, private orchestration for an opt-in face observation."""

    def __init__(
        self,
        *,
        detector: _Detector,
        aligner: Callable[[object, ScrfdFaceCandidate], object],
        quality_gate: FaceQualityGate,
        quality_metrics: Callable[[object, ScrfdFaceCandidate], FaceQualityMetrics],
        liveness: Callable[[object, ScrfdFaceCandidate], LivenessResult],
        embedder: _Embedder,
        verifier: PrivateOneToOneVerifier,
        gate: FaceVerificationGate,
        threshold_version: str = "face-verification-v1",
    ) -> None:
        if not callable(getattr(detector, "detect_single_bgr", None)) or not callable(aligner) or not isinstance(quality_gate, FaceQualityGate):
            raise ValueError("Face coordinator requires private detector, aligner and quality gate.")
        if not callable(quality_metrics) or not callable(liveness) or not callable(getattr(embedder, "embed_aligned_bgr", None)):
            raise ValueError("Face coordinator requires private quality, liveness and embedding providers.")
        if not isinstance(verifier, PrivateOneToOneVerifier) or not isinstance(gate, FaceVerificationGate) or not isinstance(threshold_version, str) or not threshold_version.strip():
            raise ValueError("Face coordinator verification policy is invalid.")
        self._detector = detector
        self._aligner = aligner
        self._quality_gate = quality_gate
        self._quality_metrics = quality_metrics
        self._liveness = liveness
        self._embedder = embedder
        self._verifier = verifier
        self._gate = gate
        self._threshold_version = threshold_version

    def observe(
        self,
        *,
        validation: FrameValidation,
        image: object,
        consent: BiometricConsent,
        enrolled: PrivateFaceEmbedding,
        observed_at: datetime,
    ) -> FaceVerification:
        """Process one validated frame without exposing biometric internals."""
        if not isinstance(validation, FrameValidation) or not validation.usable or validation.frame is None:
            raise ValueError("Face coordinator requires a validated fresh frame.")
        frame = validation.frame
        if not isinstance(consent, BiometricConsent) or not isinstance(enrolled, PrivateFaceEmbedding):
            raise ValueError("Face coordinator requires consent and a private enrolled template.")
        if consent.state(observed_at) is not ConsentState.GRANTED:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.UNAVAILABLE, LivenessResult.UNAVAILABLE, None, observed_at)
        try:
            candidate = self._detector.detect_single_bgr(image)
        except Exception:
            candidate = None
        if candidate is None:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.UNAVAILABLE, LivenessResult.UNAVAILABLE, None, observed_at)
        try:
            assessment = self._quality_gate.assess(self._quality_metrics(image, candidate))
        except Exception:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.UNAVAILABLE, LivenessResult.UNAVAILABLE, None, observed_at)
        if assessment.quality is FaceQuality.FAILED:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.FAILED, LivenessResult.UNAVAILABLE, None, observed_at)
        try:
            liveness = self._liveness(image, candidate)
        except Exception:
            liveness = LivenessResult.UNAVAILABLE
        if not isinstance(liveness, LivenessResult) or liveness is not LivenessResult.PASSED:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.PASSED, liveness if isinstance(liveness, LivenessResult) else LivenessResult.UNAVAILABLE, None, observed_at)
        try:
            aligned = self._aligner(image, candidate)
            probe = self._embedder.embed_aligned_bgr(aligned)
            similarity = self._verifier.compare(probe, enrolled).normalized_similarity
        except Exception:
            return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.UNAVAILABLE, LivenessResult.UNAVAILABLE, None, observed_at)
        return self._emit(consent, frame.stream_session_id, frame.frame_sequence, FaceQuality.PASSED, LivenessResult.PASSED, similarity, observed_at)

    def _emit(
        self, consent: BiometricConsent, stream_session_id: str, frame_sequence: int, quality: FaceQuality,
        liveness: LivenessResult, similarity: float | None, observed_at: datetime,
    ) -> FaceVerification:
        return self._gate.observe(FaceVerificationObservation(
            consent, stream_session_id, frame_sequence, self._embedder.model_id, self._embedder.model_version,
            self._threshold_version, quality, liveness, similarity, observed_at,
        ))
