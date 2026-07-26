"""The UVC producer captures, binds and publishes without needing a camera.

A fake capture stands in for the USB device, so the whole path -- frame identity,
payload binding, detector hand-off and the canonical artifact -- is provable
without hardware or a camera permission. The published document is validated
against the shared vision_summary v0.1 schema where jsonschema is installed.
"""

from datetime import UTC, datetime, timedelta
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from brain.cli.vision_uvc_producer import publish_once
from brain.vision.contracts import BoundingBox, Detection, VisionContractError
from brain.vision.uvc import UvcCameraSource, UvcCaptureError


SCHEMAS = Path(__file__).resolve().parents[1] / "shared/schemas/vision"


class _FakeCapture:
    """Yields a synthetic image, then whatever the script says."""

    def __init__(self, frames=None, opened=True):
        self._frames = list(frames) if frames is not None else [np.zeros((48, 64, 3), dtype=np.uint8)]
        self._opened = opened
        self.released = False

    def isOpened(self):  # noqa: N802 - OpenCV's name
        return self._opened

    def read(self):
        if not self._frames:
            return False, None
        image = self._frames.pop(0)
        return image is not None, image

    def get(self, _prop):
        return 30.0

    def release(self):
        self.released = True


class _StubDetector:
    """Resolves the payload by hash, then reports one fixed detection."""

    def __init__(self, resolver):
        self._resolver = resolver
        self.resolved_hashes = []

    def detect(self, frame, _produced_at):
        payload = self._resolver.resolve(frame.payload_hash)
        self.resolved_hashes.append(frame.payload_hash)
        assert isinstance(payload, bytes) and payload
        return (Detection("person", 0.88, BoundingBox(4, 4, 10, 10), "t-1"),)


def _source(capture, **kwargs):
    return UvcCameraSource(
        0, device_id="hawkeye-usb", camera_id="front_rgb",
        capture_factory=lambda _index: capture,
        now=lambda: datetime(2026, 7, 24, 21, 0, tzinfo=UTC),
        **kwargs,
    )


class UvcSourceTests(unittest.TestCase):
    def test_a_captured_frame_is_bound_to_its_payload(self) -> None:
        source = _source(_FakeCapture())
        source.open()
        frame, payload = source.capture_once()
        self.assertEqual(frame.width_px, 64)
        self.assertEqual(frame.height_px, 48)
        self.assertEqual(frame.encoding, "jpeg")
        self.assertEqual(frame.frame_sequence, 1)
        # The frame's hash is the hash of exactly these bytes.
        self.assertEqual(source.resolve(frame.payload_hash), payload)
        source.close()

    def test_resolve_refuses_a_foreign_hash(self) -> None:
        source = _source(_FakeCapture())
        source.open()
        source.capture_once()
        with self.assertRaises(UvcCaptureError):
            source.resolve("f" * 64)
        source.close()

    def test_a_failed_read_is_a_counted_drop_not_a_crash(self) -> None:
        source = _source(_FakeCapture(frames=[None]))
        source.open()
        with self.assertRaises(UvcCaptureError):
            source.capture_once()
        self.assertEqual(source.dropped_frames, 1)
        source.close()

    def test_a_device_that_will_not_open_is_refused_clearly(self) -> None:
        source = _source(_FakeCapture(opened=False))
        with self.assertRaises(UvcCaptureError):
            source.open()

    def test_it_requires_a_device_and_camera_id(self) -> None:
        with self.assertRaises(VisionContractError):
            UvcCameraSource(0, device_id=" ", camera_id="front_rgb")

    def test_sequence_advances_per_frame(self) -> None:
        capture = _FakeCapture(frames=[np.zeros((8, 8, 3), dtype=np.uint8) for _ in range(2)])
        source = _source(capture)
        source.open()
        first, _ = source.capture_once()
        second, _ = source.capture_once()
        self.assertEqual((first.frame_sequence, second.frame_sequence), (1, 2))
        source.close()


class PublishTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.artifact = Path(self._tmp.name) / "front-summary.json"

    def _publish(self, detector_factory=None):
        source = _source(_FakeCapture())
        source.open()
        detector = detector_factory(source) if detector_factory else None
        document, payload, result = publish_once(
            source, detector, artifact=self.artifact, max_age_s=1.0,
            model_id="yolo", model_version="yolo11n",
        )
        source.close()
        return document, payload, result

    def test_it_publishes_a_canonical_summary_with_detections(self) -> None:
        document, _payload, _result = self._publish(_StubDetector)
        self.assertTrue(self.artifact.exists())
        stored = json.loads(self.artifact.read_text(encoding="utf-8"))
        self.assertEqual(stored, document)
        self.assertEqual(stored["contract_version"], "v0.1")
        self.assertEqual(stored["source"], "camera:front_rgb")
        self.assertEqual(stored["detection_count"], 1)
        self.assertEqual(stored["detections"][0]["label"], "person")
        self.assertEqual(stored["detections"][0]["tracker_id"], "t-1")
        self.assertEqual(stored["max_age_s"], 1.0)

    def test_without_a_detector_it_publishes_zero_detections(self) -> None:
        document, _payload, _result = self._publish()
        self.assertEqual(document["detection_count"], 0)
        self.assertEqual(document["detections"], [])

    def test_no_temp_file_is_left_beside_the_artifact(self) -> None:
        self._publish()
        self.assertEqual([p.name for p in self.artifact.parent.iterdir()], ["front-summary.json"])

    @unittest.skipUnless(importlib.util.find_spec("jsonschema"), "jsonschema is required to validate the schema")
    def test_the_published_document_satisfies_the_shared_schema(self) -> None:
        import jsonschema

        document, _payload, _result = self._publish(_StubDetector)
        schema = json.loads((SCHEMAS / "vision_summary_v0_1.schema.json").read_text(encoding="utf-8"))
        validator_class = jsonschema.validators.validator_for(schema)
        validator_class(schema, format_checker=validator_class.FORMAT_CHECKER).validate(document)


class SafetyTests(unittest.TestCase):
    def test_the_producer_never_imports_flight_control(self) -> None:
        root = Path(__file__).resolve().parents[1]
        for source in (root / "brain/vision/uvc.py", root / "brain/cli/vision_uvc_producer.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


if __name__ == "__main__":
    unittest.main()
