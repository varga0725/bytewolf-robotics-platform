"""P0 recorded YOLO path: inference evidence through tracking and dashboard artifacts."""

from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy

from brain.cli.vision_recorded_pipeline import run_recorded_pipeline
from brain.vision.contracts import BoundingBox, Detection


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_ENCODED, SOURCE_JPEG = cv2.imencode(".jpg", numpy.zeros((120, 160, 3), dtype=numpy.uint8))


def _fixture_line(sequence: int, payload: bytes) -> str:
    return json.dumps({
        "contract_version": "camera_frame.v1",
        "device_id": "sim-01",
        "camera_id": "front-rgb",
        "stream_session_id": "recorded-yolo-e2e",
        "frame_sequence": sequence,
        "captured_at": "2026-07-21T11:59:59.995Z",
        "received_at": "2026-07-21T12:00:00Z",
        "calibration_version": "v1",
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        "encoding": "jpeg",
        "width_px": 160,
        "height_px": 120,
        "latency_ms": 5.0,
        "dropped_frames": 0,
        "payload_base64": base64.b64encode(payload).decode("ascii"),
        "detections": [],
    })


class RecordedYoloEndToEndTests(unittest.TestCase):
    def test_fake_yolo_tracks_person_and_publishes_observation_only_dashboard_artifacts(self) -> None:
        class FakeYoloDetector:
            """YOLO-shaped test double that requires the hash-bound payload resolver."""

            def __init__(self, model_id, model_version, resolver, *, weights_path):
                self.model_id = model_id
                self.model_version = model_version
                self._resolver = resolver

            def detect(self, frame, _produced_at):
                self._resolver.resolve(frame.payload_hash)
                left = 20 if frame.frame_sequence == 1 else 24
                return (Detection("person", 0.91, BoundingBox(left, 18, 32, 72)),)

        with TemporaryDirectory() as directory, patch(
            "brain.cli.vision_recorded_pipeline.UltralyticsYoloDetector", FakeYoloDetector,
        ):
            root = Path(directory)
            fixture = root / "two-frames.jsonl"
            fixture.write_text(
                _fixture_line(1, bytes(SOURCE_JPEG)) + "\n" + _fixture_line(2, bytes(SOURCE_JPEG)) + "\n",
                encoding="utf-8",
            )
            weights = root / "approved-yolo11n.pt"
            weights.write_bytes(b"test-only-local-weights")
            status = root / "status.json"
            overlay = root / "overlay.jpg"

            report = run_recorded_pipeline(
                fixture,
                status,
                overlay,
                now=NOW,
                detector="yolo",
                weights_path=weights,
            )

            dashboard = json.loads(status.read_text(encoding="utf-8"))
            self.assertEqual(report["detector"], "yolo")
            self.assertEqual(report["model_id"], "research-yolo11n")
            self.assertEqual(report["model_version"], "approved-yolo11n.pt")
            self.assertEqual(report["processed_frames"], 2)
            self.assertEqual(report["benchmark"]["sample_count"], 2)
            self.assertEqual(dashboard["contract_version"], "vision_dashboard.v1")
            self.assertEqual(dashboard["state"], "valid")
            self.assertEqual(dashboard["track_count"], 1)
            self.assertEqual(dashboard["detections"][0]["tracker_id"], "local-000001")
            self.assertEqual(dashboard["detections"][0]["bounding_box"]["x_px"], 24)
            self.assertTrue(overlay.read_bytes().startswith(b"\xff\xd8"))
            self.assertIsNotNone(cv2.imdecode(numpy.frombuffer(overlay.read_bytes(), dtype=numpy.uint8), cv2.IMREAD_COLOR))
            self.assertFalse(
                {"command", "mission", "actuator", "embedding", "payload", "evidence_path"} & set(dashboard),
            )


if __name__ == "__main__":
    unittest.main()
