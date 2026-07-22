from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.vision.recorded import AnnotatedFixtureDetector, RecordedFixtureError, RecordedJsonlIngest
from brain.vision.runtime import RuntimeState, VisionRuntime


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def record(sequence: int = 1, payload: bytes = b"fixture-jpeg") -> dict[str, object]:
    return {
        "contract_version": "camera_frame.v1", "device_id": "sim-01", "camera_id": "front-rgb",
        "stream_session_id": "recording-01", "frame_sequence": sequence,
        "captured_at": "2026-07-21T11:59:59.995Z", "received_at": "2026-07-21T12:00:00Z",
        "calibration_version": "cal-v1", "payload_hash": hashlib.sha256(payload).hexdigest(),
        "encoding": "jpeg", "width_px": 640, "height_px": 480, "latency_ms": 5.0,
        "dropped_frames": 0, "payload_base64": base64.b64encode(payload).decode("ascii"),
        "detections": [{"label": "person", "confidence": .9, "bounding_box": {"x_px": 3, "y_px": 4, "width_px": 30, "height_px": 40}, "tracker_id": "track-1"}],
    }


class RecordedIngestTests(unittest.TestCase):
    def test_jsonl_payload_hash_and_annotated_detections_drive_runtime(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.jsonl"
            path.write_text(json.dumps(record()) + "\n")
            source = RecordedJsonlIngest(path)
            runtime = VisionRuntime(AnnotatedFixtureDetector(source))

            self.assertTrue(runtime.ingest_once(source))
            outcome = runtime.process_next(NOW)

            self.assertEqual(outcome.state, RuntimeState.PROCESSED)
            self.assertEqual(outcome.detection.detections[0].label, "person")  # type: ignore[union-attr]
            self.assertEqual(source.payload_for(outcome.frame), b"fixture-jpeg")  # type: ignore[arg-type]
            self.assertFalse(runtime.ingest_once(source))

    def test_hash_mismatch_is_rejected_at_ingest_boundary(self) -> None:
        invalid = record()
        invalid["payload_hash"] = "0" * 64
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.jsonl"
            path.write_text(json.dumps(invalid) + "\n")

            with self.assertRaisesRegex(RecordedFixtureError, "payload hash"):
                RecordedJsonlIngest(path).poll()

    def test_invalid_annotation_cannot_become_an_empty_detection(self) -> None:
        invalid = record()
        invalid["detections"] = [{"label": "person", "confidence": 1.2, "bounding_box": {"x_px": 0, "y_px": 0, "width_px": 1, "height_px": 1}}]
        with TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.jsonl"
            path.write_text(json.dumps(invalid) + "\n")

            with self.assertRaises(RecordedFixtureError):
                RecordedJsonlIngest(path).poll()


if __name__ == "__main__":
    unittest.main()
