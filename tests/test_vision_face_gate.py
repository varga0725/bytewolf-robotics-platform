"""P1 multi-frame face-verification confirmation gate tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.face_gate import FaceVerificationGate, FaceVerificationObservation
from brain.vision.face_verification import BiometricConsent, FaceQuality, LivenessResult
from brain.vision.contracts import ResultState


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
SUBJECT = "sub_0123456789abcdef0123456789abcdef"


def consent(**overrides: object) -> BiometricConsent:
    document: dict[str, object] = {
        "subject_id": SUBJECT, "consent_record_id": "consent-0123456789abcdef",
        "granted_at": NOW - timedelta(days=1), "expires_at": NOW + timedelta(days=1),
    }
    return BiometricConsent(**{**document, **overrides})  # type: ignore[arg-type]


def observation(sequence: int, score: float, **overrides: object) -> FaceVerificationObservation:
    document: dict[str, object] = {
        "consent": consent(), "stream_session_id": "session-a", "frame_sequence": sequence,
        "model_id": "research-arcface", "model_version": "r100-v1", "threshold_version": "v1",
        "quality": FaceQuality.PASSED, "liveness": LivenessResult.PASSED,
        "similarity": score, "observed_at": NOW + timedelta(milliseconds=sequence),
    }
    return FaceVerificationObservation(**{**document, **overrides})  # type: ignore[arg-type]


class FaceVerificationGateTests(unittest.TestCase):
    def test_requires_multiple_frames_then_applies_cooldown(self) -> None:
        gate = FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=3, cooldown=timedelta(seconds=5))

        first = gate.observe(observation(1, 0.9))
        second = gate.observe(observation(2, 0.72))  # retained by hysteresis
        confirmed = gate.observe(observation(3, 0.9))
        cooling = gate.observe(observation(4, 0.99))

        self.assertEqual(first.state, ResultState.MISSING)
        self.assertEqual(second.frame_sequences, (1, 2))
        self.assertEqual(confirmed.state, ResultState.VALID)
        self.assertEqual(confirmed.reason_code, "confirmed")
        self.assertEqual(cooling.reason_code, "cooldown_active")
        self.assertEqual(cooling.state, ResultState.MISSING)

    def test_low_score_resets_candidate_and_returns_final_not_matched(self) -> None:
        gate = FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=2, cooldown=timedelta())

        gate.observe(observation(1, 0.9))
        result = gate.observe(observation(2, 0.2))
        next_result = gate.observe(observation(3, 0.9))

        self.assertEqual(result.state, ResultState.VALID)
        self.assertEqual(result.reason_code, "not_matched")
        self.assertEqual(next_result.state, ResultState.MISSING)
        self.assertEqual(next_result.frame_sequences, (3,))

    def test_liveness_consent_and_sequence_failures_are_never_matches(self) -> None:
        gate = FaceVerificationGate(acceptance_threshold=0.8, continuation_threshold=0.7, confirmation_frames=1, cooldown=timedelta())
        liveness = gate.observe(observation(1, 0.99, liveness=LivenessResult.FAILED))
        revoked = gate.observe(observation(2, 0.99, consent=consent(revoked_at=NOW)))
        gate.observe(observation(3, 0.99))
        replay = gate.observe(observation(3, 0.99))

        self.assertEqual(liveness.state, ResultState.INVALID)
        self.assertEqual(liveness.reason_code, "liveness_failed")
        self.assertEqual(revoked.reason_code, "consent_revoked")
        self.assertEqual(replay.state, ResultState.INVALID)
        self.assertEqual(replay.reason_code, "source_invalid")


if __name__ == "__main__":
    unittest.main()
