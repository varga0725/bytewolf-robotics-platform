"""Private SCRFD-to-ArcFace verification coordinator tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.face_alignment import ScrfdFaceCandidate
from brain.vision.face_coordinator import FaceVerificationCoordinator
from brain.vision.face_embedding import PrivateFaceEmbedding, PrivateOneToOneVerifier
from brain.vision.face_gate import FaceVerificationGate
from brain.vision.face_quality import FaceQualityGate, FaceQualityMetrics
from brain.vision.face_verification import BiometricConsent, FaceQuality, LivenessResult, MatchResult
from brain.vision.contracts import CAMERA_FRAME_V1, CameraFrame, FrameValidation, ResultState


NOW = datetime(2026, 7, 22, 15, 0, tzinfo=UTC)
SUBJECT = "sub_0123456789abcdef0123456789abcdef"


def consent() -> BiometricConsent:
    return BiometricConsent(SUBJECT, "consent-0123456789abcdef", NOW - timedelta(days=1), NOW + timedelta(days=1))


def embedding() -> PrivateFaceEmbedding:
    return PrivateFaceEmbedding("research-arcface", "buffalo-l-v0.7", (1.0,) + (0.0,) * 511)


def validation(state: ResultState = ResultState.VALID) -> FrameValidation:
    frame = CameraFrame(
        CAMERA_FRAME_V1, "device-a", "front-rgb", "session-a", 1, NOW, NOW,
        "calibration-v1", "a" * 64, "jpeg", 128, 128, 1.0, 0,
    )
    return FrameValidation(state, frame, "test")


class _Detector:
    def __init__(self, face: ScrfdFaceCandidate | None) -> None:
        self.face = face
        self.calls = 0

    def detect_single_bgr(self, _image: object) -> ScrfdFaceCandidate | None:
        self.calls += 1
        return self.face


class _Embedder:
    model_id = "research-arcface"
    model_version = "buffalo-l-v0.7"

    def __init__(self) -> None:
        self.calls = 0

    def embed_aligned_bgr(self, _image: object) -> PrivateFaceEmbedding:
        self.calls += 1
        return embedding()


def face() -> ScrfdFaceCandidate:
    return ScrfdFaceCandidate(
        "research-scrfd-10gf", "buffalo-l-v0.7", 0.95, (20.0, 20.0, 100.0, 110.0),
        ((40.0, 50.0), (70.0, 50.0), (55.0, 65.0), (43.0, 88.0), (68.0, 88.0)),
    )


def quality_gate() -> FaceQualityGate:
    return FaceQualityGate(
        threshold_version="quality-v1", minimum_face_px=64, minimum_blur_variance=20,
        minimum_luma=40, maximum_luma=220, maximum_yaw_degrees=25,
        maximum_pitch_degrees=20, maximum_roll_degrees=20,
    )


def metrics(_image: object, _face: ScrfdFaceCandidate) -> FaceQualityMetrics:
    return FaceQualityMetrics(80, 90, 100.0, 128.0, 0.0, 0.0, 0.0)


class FaceVerificationCoordinatorTests(unittest.TestCase):
    def test_runs_private_pipeline_in_required_order_and_returns_only_evidence(self) -> None:
        embedder = _Embedder()
        coordinator = FaceVerificationCoordinator(
            detector=_Detector(face()), aligner=lambda _image, _face: object(), quality_gate=quality_gate(),
            quality_metrics=metrics, liveness=lambda _image, _face: LivenessResult.PASSED,
            embedder=embedder, verifier=PrivateOneToOneVerifier(),
            gate=FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=1, cooldown=timedelta()),
        )

        result = coordinator.observe(validation=validation(), image=object(), consent=consent(), enrolled=embedding(), observed_at=NOW)

        self.assertEqual(result.state, ResultState.VALID)
        self.assertEqual(result.match, MatchResult.MATCHED)
        self.assertEqual(result.reason_code, "confirmed")
        self.assertEqual(embedder.calls, 1)
        self.assertNotIn("embedding", result.__dataclass_fields__)
        self.assertNotIn("landmarks", result.__dataclass_fields__)

    def test_quality_failure_prevents_alignment_embedding_and_comparison(self) -> None:
        embedder = _Embedder()
        align_calls = 0

        def aligner(_image: object, _face: ScrfdFaceCandidate) -> object:
            nonlocal align_calls
            align_calls += 1
            return object()

        coordinator = FaceVerificationCoordinator(
            detector=_Detector(face()), aligner=aligner, quality_gate=quality_gate(),
            quality_metrics=lambda _image, _face: FaceQualityMetrics(20, 20, 100.0, 128.0, 0.0, 0.0, 0.0),
            liveness=lambda _image, _face: LivenessResult.PASSED, embedder=embedder,
            verifier=PrivateOneToOneVerifier(), gate=FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=1, cooldown=timedelta()),
        )

        result = coordinator.observe(validation=validation(), image=object(), consent=consent(), enrolled=embedding(), observed_at=NOW)

        self.assertEqual(result.quality, FaceQuality.FAILED)
        self.assertEqual(result.reason_code, "quality_failed")
        self.assertEqual(align_calls, 0)
        self.assertEqual(embedder.calls, 0)

    def test_missing_or_ambiguous_face_is_model_unavailable(self) -> None:
        coordinator = FaceVerificationCoordinator(
            detector=_Detector(None), aligner=lambda _image, _face: object(), quality_gate=quality_gate(),
            quality_metrics=metrics, liveness=lambda _image, _face: LivenessResult.PASSED,
            embedder=_Embedder(), verifier=PrivateOneToOneVerifier(),
            gate=FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=1, cooldown=timedelta()),
        )

        result = coordinator.observe(validation=validation(), image=object(), consent=consent(), enrolled=embedding(), observed_at=NOW)

        self.assertEqual(result.state, ResultState.INVALID)
        self.assertEqual(result.reason_code, "model_unavailable")
        self.assertEqual(result.match, MatchResult.UNAVAILABLE)

    def test_refuses_unvalidated_or_stale_frame_before_detector_runs(self) -> None:
        detector = _Detector(face())
        coordinator = FaceVerificationCoordinator(
            detector=detector, aligner=lambda _image, _face: object(), quality_gate=quality_gate(),
            quality_metrics=metrics, liveness=lambda _image, _face: LivenessResult.PASSED,
            embedder=_Embedder(), verifier=PrivateOneToOneVerifier(),
            gate=FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=1, cooldown=timedelta()),
        )

        with self.assertRaisesRegex(ValueError, "validated"):
            coordinator.observe(validation=validation(ResultState.STALE), image=object(), consent=consent(), enrolled=embedding(), observed_at=NOW)

        self.assertEqual(detector.calls, 0)
