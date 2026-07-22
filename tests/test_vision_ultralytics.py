from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, CameraFrame
from brain.vision.ultralytics import PayloadIntegrityError, UltralyticsYoloDetector


NOW = datetime(2026, 7, 21, 12, tzinfo=UTC)
PAYLOAD = b"jpeg-payload"


def frame(payload_hash: str | None = None) -> CameraFrame:
    return CameraFrame(
        CAMERA_FRAME_V1, "device", "front", "session", 1, NOW, NOW, "cal-v1",
        payload_hash or hashlib.sha256(PAYLOAD).hexdigest(), "jpeg", 10, 10, 0, 0,
    )


class _Resolver:
    def resolve(self, _payload_hash: str) -> bytes:
        return PAYLOAD


class _Model:
    names = {0: "person"}

    def __call__(self, image, *, verbose: bool):
        self.image = image
        self.verbose = verbose
        return (_Result(),)


class _Result:
    boxes = type("Boxes", (), {"xyxy": [[1, 2, 7, 9]], "conf": [0.8], "cls": [0]})()


class UltralyticsYoloDetectorTests(unittest.TestCase):
    def test_resolves_hash_checked_payload_and_maps_yolo_boxes(self) -> None:
        model = _Model()
        detector = UltralyticsYoloDetector("research-yolo", "weights-v1", _Resolver(), model=model, decoder=lambda _: object())

        detections = detector.detect(frame(), NOW)

        self.assertEqual(detector.model_id, "research-yolo")
        self.assertEqual(detections[0].label, "person")
        self.assertEqual(detections[0].bounding_box.width_px, 6)
        self.assertEqual(detections[0].bounding_box.height_px, 7)

    def test_refuses_payload_that_does_not_match_camera_frame_hash(self) -> None:
        detector = UltralyticsYoloDetector("research-yolo", "weights-v1", _Resolver(), model=_Model(), decoder=lambda _: object())

        with self.assertRaises(PayloadIntegrityError):
            detector.detect(frame("a" * 64), NOW)


if __name__ == "__main__":
    unittest.main()
