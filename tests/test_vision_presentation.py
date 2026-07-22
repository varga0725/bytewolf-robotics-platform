from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.contracts import (
    CAMERA_FRAME_V1,
    DETECTION_RESULT_V1,
    BoundingBox,
    CameraFrame,
    Detection,
    DetectionResult,
    VisionHealth,
)
from brain.vision.presentation import VisionArtifactPublisher, vision_status_document


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def frame() -> CameraFrame:
    return CameraFrame(
        CAMERA_FRAME_V1, "sim-01", "front-rgb", "session-01", 1, NOW, NOW,
        "cal-v1", "a" * 64, "jpeg", 640, 480, 0.0, 0,
    )


class VisionPresentationTests(unittest.TestCase):
    def test_status_document_exposes_observations_and_health_not_control(self) -> None:
        result = DetectionResult(
            DETECTION_RESULT_V1, frame(), "stub", "v1", NOW,
            (Detection("person", .9, BoundingBox(1, 2, 30, 40), "track-1"),),
        )
        health = VisionHealth(NOW, "healthy", "healthy", "missing", 0, 2)

        document = vision_status_document(result, health, now=NOW)

        self.assertEqual(document["state"], "missing")
        self.assertEqual(document["track_count"], 1)
        self.assertEqual(document["detections"][0]["tracker_id"], "track-1")
        self.assertNotIn("command", json.dumps(document).lower())
        self.assertNotIn("mission", json.dumps(document).lower())

    def test_publisher_writes_status_and_rendered_frame_atomically(self) -> None:
        health = VisionHealth(NOW, "healthy", "healthy", "healthy", 0, 0)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            publisher = VisionArtifactPublisher(root / "status.json", root / "frame.jpg")
            publisher.publish(None, health, now=NOW, render=lambda _result: b"jpeg-overlay")

            self.assertEqual(json.loads((root / "status.json").read_text()), vision_status_document(None, health, now=NOW))
            self.assertEqual((root / "frame.jpg").read_bytes(), b"jpeg-overlay")
            self.assertFalse((root / "status.json.tmp").exists())
            self.assertFalse((root / "frame.jpg.tmp").exists())


if __name__ == "__main__":
    unittest.main()
