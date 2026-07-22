from __future__ import annotations

from datetime import UTC, datetime
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, DETECTION_RESULT_V1, BoundingBox, CameraFrame, Detection, DetectionResult
from brain.vision.overlay import OverlayRenderError, render_jpeg_overlay


class VisionOverlayTests(unittest.TestCase):
    def test_rejects_non_image_payload(self) -> None:
        with self.assertRaises(OverlayRenderError):
            render_jpeg_overlay(b"not-an-image", None)

    def test_renders_a_jpeg_overlay(self) -> None:
        import cv2
        import numpy
        now = datetime(2026, 7, 21, tzinfo=UTC)
        _, payload = cv2.imencode(".jpg", numpy.zeros((30, 30, 3), dtype=numpy.uint8))
        frame = CameraFrame(CAMERA_FRAME_V1, "d", "c", "s", 1, now, now, "cal", "a" * 64, "jpeg", 30, 30, 0, 0)
        result = DetectionResult(DETECTION_RESULT_V1, frame, "stub", "v1", now, (Detection("person", .9, BoundingBox(2, 2, 20, 20), "track-1"),))

        rendered = render_jpeg_overlay(bytes(payload), result)

        self.assertTrue(rendered.startswith(b"\xff\xd8"))
        self.assertNotEqual(rendered, bytes(payload))


if __name__ == "__main__":
    unittest.main()
