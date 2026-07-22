"""Deterministic image-derived face quality metric tests."""

from __future__ import annotations

import unittest

import numpy

from brain.vision.face_alignment import ScrfdFaceCandidate
from brain.vision.face_quality import FacePoseEstimate, extract_face_quality_metrics_bgr


def candidate() -> ScrfdFaceCandidate:
    return ScrfdFaceCandidate("research-scrfd-10gf", "v1", 0.9, (2.0, 2.0, 10.0, 10.0), ((3.0, 4.0), (7.0, 4.0), (5.0, 6.0), (3.0, 8.0), (7.0, 8.0)))


class FaceQualityMetricTests(unittest.TestCase):
    def test_extracts_deterministic_size_luma_blur_and_roll(self) -> None:
        image = numpy.full((12, 12, 3), (10, 20, 30), dtype=numpy.uint8)

        result = extract_face_quality_metrics_bgr(image, candidate(), pose=FacePoseEstimate(1.0, -2.0))

        self.assertEqual((result.face_width_px, result.face_height_px), (8, 8))
        self.assertEqual(result.mean_luma, 22.0)
        self.assertEqual(result.blur_variance, 0.0)
        self.assertEqual(result.roll_degrees, 0.0)
        self.assertEqual((result.yaw_degrees, result.pitch_degrees), (1.0, -2.0))

    def test_refuses_uncalibrated_pose_or_invalid_image_data(self) -> None:
        image = numpy.zeros((12, 12, 3), dtype=numpy.uint8)
        with self.assertRaises(ValueError):
            extract_face_quality_metrics_bgr(image, candidate(), pose=None)
        with self.assertRaises(ValueError):
            extract_face_quality_metrics_bgr(numpy.zeros((12, 12), dtype=numpy.uint8), candidate(), pose=FacePoseEstimate(0.0, 0.0))
        with self.assertRaises(ValueError):
            extract_face_quality_metrics_bgr(image, candidate(), pose=FacePoseEstimate(float("nan"), 0.0))
