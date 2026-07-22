"""Five-landmark face-alignment tests for the private P1 research seam."""

from __future__ import annotations

import unittest

import numpy

from brain.vision.face_alignment import ScrfdFaceCandidate, align_five_point_bgr


_TARGET_LANDMARKS = (
    (38.2946, 51.6963),
    (73.5318, 51.5014),
    (56.0252, 71.7366),
    (41.5493, 92.3655),
    (70.7299, 92.2041),
)


def candidate(**overrides: object) -> ScrfdFaceCandidate:
    document: dict[str, object] = {
        "model_id": "research-scrfd-10gf",
        "model_version": "2026.07",
        "confidence": 0.92,
        "bounds_xyxy": (10.0, 20.0, 110.0, 120.0),
        "landmarks_xy": _TARGET_LANDMARKS,
    }
    return ScrfdFaceCandidate(**{**document, **overrides})  # type: ignore[arg-type]


class ScrfdFaceCandidateTests(unittest.TestCase):
    def test_accepts_a_finite_face_candidate_with_five_ordered_landmarks(self) -> None:
        face = candidate()

        self.assertEqual(face.bounds_xyxy, (10.0, 20.0, 110.0, 120.0))
        self.assertEqual(face.landmarks_xy, _TARGET_LANDMARKS)

    def test_rejects_invalid_detection_provenance_geometry_and_landmarks(self) -> None:
        invalid = (
            {"model_id": ""},
            {"confidence": 1.01},
            {"bounds_xyxy": (10.0, 20.0, 10.0, 120.0)},
            {"landmarks_xy": _TARGET_LANDMARKS[:4]},
            {"landmarks_xy": ((float("nan"), 1.0),) * 5},
        )

        for overrides in invalid:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValueError):
                    candidate(**overrides)


class FivePointFaceAlignmentTests(unittest.TestCase):
    def test_aligns_bgr_image_to_private_arcface_crop(self) -> None:
        image = numpy.zeros((144, 128, 3), dtype=numpy.uint8)
        for index, (x, y) in enumerate(_TARGET_LANDMARKS, start=1):
            image[round(y), round(x)] = (index, index, index)

        aligned = align_five_point_bgr(image, candidate())

        self.assertEqual(aligned.shape, (112, 112, 3))
        self.assertEqual(aligned.dtype, numpy.uint8)
        self.assertGreater(int(aligned.sum()), 0)

    def test_rejects_non_bgr_and_degenerate_alignment_input(self) -> None:
        with self.assertRaises(ValueError):
            align_five_point_bgr(numpy.zeros((112, 112), dtype=numpy.uint8), candidate())
        degenerate = candidate(landmarks_xy=((20.0, 20.0),) * 5)
        with self.assertRaises(ValueError):
            align_five_point_bgr(numpy.zeros((144, 128, 3), dtype=numpy.uint8), degenerate)
        outside = candidate(landmarks_xy=((-1.0, 2.0),) + _TARGET_LANDMARKS[1:])
        with self.assertRaises(ValueError):
            align_five_point_bgr(numpy.zeros((144, 128, 3), dtype=numpy.uint8), outside)
