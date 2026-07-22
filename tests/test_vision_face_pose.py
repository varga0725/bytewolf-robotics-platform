"""Calibrated five-landmark PnP pose adapter tests."""

from __future__ import annotations

import unittest

import cv2
import numpy

from brain.vision.face_alignment import ScrfdFaceCandidate
from brain.vision.face_pose import CalibratedFiveLandmarkPoseAdapter, FiveLandmarkCameraCalibration


class FacePoseTests(unittest.TestCase):
    def test_recovers_front_facing_synthetic_points_and_refuses_calibration_mismatch(self) -> None:
        calibration = FiveLandmarkCameraCalibration("front-v1", 640, 480, 600.0, 600.0, 320.0, 240.0, (0, 0, 0, 0, 0), 0.1)
        model = numpy.array(((-30, 35, -30), (30, 35, -30), (0, 0, 0), (-25, -35, -20), (25, -35, -20)), dtype=numpy.float64)
        matrix = numpy.array(((600, 0, 320), (0, 600, 240), (0, 0, 1)), dtype=numpy.float64)
        points, _ = cv2.projectPoints(model, numpy.zeros((3, 1)), numpy.array(((0,), (0,), (600.0,))), matrix, numpy.zeros(5))
        landmarks = tuple(tuple(float(v) for v in point) for point in points.reshape(5, 2))
        face = ScrfdFaceCandidate("scrfd", "v1", 0.9, (250, 150, 390, 330), landmarks)
        adapter = CalibratedFiveLandmarkPoseAdapter(calibration)

        pose = adapter.estimate_bgr(numpy.zeros((480, 640, 3), dtype=numpy.uint8), face, calibration_version="front-v1")

        self.assertAlmostEqual(pose.yaw_degrees, 0.0, places=3)
        self.assertAlmostEqual(pose.pitch_degrees, 0.0, places=3)
        with self.assertRaises(ValueError):
            adapter.estimate_bgr(numpy.zeros((480, 640, 3), dtype=numpy.uint8), face, calibration_version="other")
