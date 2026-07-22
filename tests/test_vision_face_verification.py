"""P1 opt-in, observation-only face-verification contract tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.face_verification import (
    FACE_VERIFICATION_V1,
    BiometricConsent,
    ConsentState,
    FaceQuality,
    FaceVerification,
    FaceVerificationError,
    LivenessResult,
    MatchResult,
)
from brain.vision.contracts import ResultState


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)
SUBJECT = "sub_0123456789abcdef0123456789abcdef"


def consent(**overrides: object) -> BiometricConsent:
    document: dict[str, object] = {
        "subject_id": SUBJECT,
        "consent_record_id": "consent-0123456789abcdef",
        "granted_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=1),
    }
    return BiometricConsent(**{**document, **overrides})  # type: ignore[arg-type]


def verification(**overrides: object) -> FaceVerification:
    document: dict[str, object] = {
        "contract_version": FACE_VERIFICATION_V1,
        "subject_id": SUBJECT,
        "consent_record_id": "consent-0123456789abcdef",
        "consent_state": ConsentState.GRANTED,
        "stream_session_id": "session-a",
        "frame_sequences": (10, 11, 12),
        "model_id": "research-arcface",
        "model_version": "r100-v1",
        "threshold_version": "face-threshold-v1",
        "quality": FaceQuality.PASSED,
        "liveness": LivenessResult.PASSED,
        "match": MatchResult.MATCHED,
        "confidence": 0.91,
        "state": ResultState.VALID,
        "reason_code": "confirmed",
        "produced_at": NOW,
    }
    return FaceVerification(**{**document, **overrides})  # type: ignore[arg-type]


class BiometricConsentTests(unittest.TestCase):
    def test_consent_is_immutable_and_expires_or_revokes_fail_closed(self) -> None:
        granted = consent()
        revoked = consent(revoked_at=NOW - timedelta(seconds=1))

        self.assertEqual(granted.state(NOW), ConsentState.GRANTED)
        self.assertFalse(revoked.allows_verification(NOW))
        with self.assertRaises(FrozenInstanceError):
            granted.revoked_at = NOW  # type: ignore[misc]

    def test_rejects_non_pseudonymous_subject_identifier(self) -> None:
        with self.assertRaisesRegex(FaceVerificationError, "pseudonymous"):
            consent(subject_id="Alice Example")


class FaceVerificationContractTests(unittest.TestCase):
    def test_valid_opt_in_confirmation_is_immutable_and_observation_only(self) -> None:
        result = verification()

        self.assertEqual(result.state, ResultState.VALID)
        self.assertEqual(result.match, MatchResult.MATCHED)
        names = {field.name for field in fields(result)}
        self.assertFalse({"embedding", "template", "payload", "command"} & names)
        with self.assertRaises(FrozenInstanceError):
            result.confidence = 0.1  # type: ignore[misc]

    def test_liveness_failure_cannot_be_represented_as_a_match(self) -> None:
        with self.assertRaisesRegex(FaceVerificationError, "liveness"):
            verification(
                liveness=LivenessResult.FAILED,
                match=MatchResult.MATCHED,
                confidence=0.99,
                state=ResultState.VALID,
                reason_code="confirmed",
            )

    def test_revoked_consent_cannot_be_represented_as_a_valid_match(self) -> None:
        with self.assertRaisesRegex(FaceVerificationError, "consent"):
            verification(consent_state=ConsentState.REVOKED)

    def test_low_quality_and_pending_multiframe_outcomes_fail_closed(self) -> None:
        low_quality = verification(
            quality=FaceQuality.FAILED, liveness=LivenessResult.UNAVAILABLE,
            match=MatchResult.UNAVAILABLE, confidence=None, state=ResultState.INVALID,
            reason_code="quality_failed",
        )
        pending = verification(
            frame_sequences=(10,), match=MatchResult.UNAVAILABLE, confidence=None,
            state=ResultState.MISSING, reason_code="multiframe_pending",
        )

        self.assertEqual(low_quality.reason_code, "quality_failed")
        self.assertEqual(pending.state, ResultState.MISSING)


if __name__ == "__main__":
    unittest.main()
