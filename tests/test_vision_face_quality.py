"""P1 deterministic face-quality gate tests."""

from __future__ import annotations

import unittest

from brain.vision.face_quality import FaceQualityGate, FaceQualityMetrics, FaceQualityReason
from brain.vision.face_verification import FaceQuality


def metrics(**overrides: object) -> FaceQualityMetrics:
    document: dict[str, object] = {
        "face_width_px": 128, "face_height_px": 128, "blur_variance": 120.0,
        "mean_luma": 128.0, "yaw_degrees": 5.0, "pitch_degrees": 2.0, "roll_degrees": 1.0,
    }
    return FaceQualityMetrics(**{**document, **overrides})  # type: ignore[arg-type]


class FaceQualityGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.gate = FaceQualityGate(
            threshold_version="face-quality-v1", minimum_face_px=96, minimum_blur_variance=80,
            minimum_luma=45, maximum_luma=210, maximum_yaw_degrees=25,
            maximum_pitch_degrees=20, maximum_roll_degrees=15,
        )

    def test_accepts_a_well_lit_sharp_frontal_face_with_deterministic_score(self) -> None:
        result = self.gate.assess(metrics())

        self.assertEqual(result.quality, FaceQuality.PASSED)
        self.assertEqual(result.reason, FaceQualityReason.ACCEPTED)
        self.assertEqual(result.threshold_version, "face-quality-v1")
        self.assertGreater(result.score, 0.8)

    def test_refuses_small_blurry_badly_lit_or_extreme_pose_faces(self) -> None:
        cases = {
            FaceQualityReason.FACE_TOO_SMALL: metrics(face_width_px=80),
            FaceQualityReason.BLURRED: metrics(blur_variance=79.9),
            FaceQualityReason.UNDEREXPOSED: metrics(mean_luma=44.9),
            FaceQualityReason.OVEREXPOSED: metrics(mean_luma=210.1),
            FaceQualityReason.POSE_OUT_OF_BOUNDS: metrics(yaw_degrees=25.1),
        }
        for expected, sample in cases.items():
            with self.subTest(expected=expected):
                result = self.gate.assess(sample)
                self.assertEqual(result.quality, FaceQuality.FAILED)
                self.assertEqual(result.reason, expected)
                self.assertIsNone(result.score)

    def test_rejects_invalid_non_finite_metrics_at_the_boundary(self) -> None:
        with self.assertRaisesRegex(ValueError, "finite"):
            metrics(blur_variance=float("nan"))


if __name__ == "__main__":
    unittest.main()
