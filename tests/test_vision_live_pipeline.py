"""Live GStreamer CLI orchestration tests without Gst or model dependencies."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from brain.cli.vision_live_pipeline import HashVerifiedGStreamerSource, run_live_pipeline
from brain.vision.contracts import BoundingBox, Detection
from brain.vision.gstreamer import GStreamerIngestAdapter, GStreamerIngestError, StreamBinding


_NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class FakeBuffer:
    captured_at = _NOW
    mime_type = "image/jpeg"
    width_px = 2
    height_px = 2

    def __init__(self, payload: bytes, *, captured_at: datetime = _NOW) -> None:
        self._payload = payload
        self.captured_at = captured_at

    def payload_bytes(self) -> bytes:
        return self._payload


class FakePipeline:
    def __init__(self, items: list[FakeBuffer | Exception | None], binding: StreamBinding) -> None:
        self.binding = binding
        self._items = deque(items)
        self.last_payload: bytes | None = None

    def pull_buffer(self) -> FakeBuffer | None:
        if not self._items:
            return None
        item = self._items.popleft()
        if isinstance(item, Exception):
            raise item
        if item is not None:
            self.last_payload = item.payload_bytes()
        return item


class FakeDetector:
    model_id = "fake-yolo"
    model_version = "test-v1"

    def __init__(self) -> None:
        self.hashes: list[str] = []

    def detect(self, frame, _now):  # type: ignore[no-untyped-def]
        self.hashes.append(frame.payload_hash)
        return (Detection("person", 0.9, BoundingBox(0, 0, 1, 1)),)


class LiveVisionPipelineTests(unittest.TestCase):
    def make_source(self, items: list[FakeBuffer | Exception | None]) -> tuple[HashVerifiedGStreamerSource, StreamBinding]:
        binding = StreamBinding("drone-1", "front-1", "session-a")
        pipeline = FakePipeline(items, binding)
        adapter = GStreamerIngestAdapter(pipeline, binding=binding, calibration_version="cal-v1", clock=lambda: _NOW)
        return HashVerifiedGStreamerSource(adapter, pipeline), binding

    def test_jpeg_is_hash_bound_detected_tracked_and_published(self) -> None:
        source, _binding = self.make_source([FakeBuffer(_JPEG)])
        detector = FakeDetector()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_live_pipeline(
                source, detector, status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=1,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["processed_frames"], 1)
            self.assertEqual(len(detector.hashes), 1)
            self.assertEqual(status["detections"][0]["tracker_id"], "local-000001")
            self.assertEqual((root / "frame.jpg").read_bytes()[:2], b"\xff\xd8")
            self.assertEqual(source.resolve(detector.hashes[0]), _JPEG)

    def test_source_error_marks_health_unavailable_and_requires_rotated_session(self) -> None:
        source, binding = self.make_source([FakeBuffer(_JPEG), RuntimeError("appsink closed")])
        detector = FakeDetector()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_live_pipeline(
                source, detector, status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=2,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["unavailable_frames"], 1)
            self.assertEqual(status["stream_state"], "unavailable")
            with self.assertRaisesRegex(ValueError, "rotate"):
                source.reconnect(binding.stream_session_id)

    def test_rotated_source_recovers_after_error_and_returns_to_healthy(self) -> None:
        first_source, _binding = self.make_source([FakeBuffer(_JPEG), RuntimeError("appsink closed")])
        recovered_binding = StreamBinding("drone-1", "front-1", "session-b")
        recovered_pipeline = FakePipeline([FakeBuffer(_JPEG)], recovered_binding)
        recovered_adapter = GStreamerIngestAdapter(
            recovered_pipeline, binding=recovered_binding, calibration_version="cal-v1", clock=lambda: _NOW,
        )
        recovered_source = HashVerifiedGStreamerSource(recovered_adapter, recovered_pipeline)
        factory_calls: list[tuple[int, str]] = []

        def reconnect_factory(attempt: int, previous_session: str) -> HashVerifiedGStreamerSource:
            factory_calls.append((attempt, previous_session))
            return recovered_source

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_live_pipeline(
                first_source, FakeDetector(), status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=3,
                reconnect_factory=reconnect_factory, max_reconnects=1,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["processed_frames"], 2)
            self.assertEqual(report["unavailable_frames"], 1)
            self.assertEqual(report["reconnects"], 1)
            self.assertEqual(report["stream_session_id"], "session-b")
            self.assertEqual(factory_calls, [(1, "session-a")])
            self.assertEqual(status["stream_state"], "healthy")

    def test_report_aggregates_processed_latency_and_incremental_frame_drops(self) -> None:
        source, _binding = self.make_source([
            FakeBuffer(_JPEG, captured_at=_NOW - timedelta(milliseconds=100)),
            FakeBuffer(_JPEG, captured_at=_NOW - timedelta(milliseconds=300)),
        ])
        source.adapter.record_dropped_frames(2)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_live_pipeline(
                source, FakeDetector(), status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=2,
            )
            self.assertEqual(report["benchmark"], {
                "sample_count": 2,
                "p50_latency_ms": 200.0,
                "p95_latency_ms": 300.0,
                "dropped_frames": 2,
            })

    def test_no_buffer_does_not_publish_fake_frame_or_observation(self) -> None:
        source, _binding = self.make_source([None])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_live_pipeline(
                source, FakeDetector(), status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=1,
            )
            self.assertEqual(report["processed_frames"], 0)
            self.assertIsNone(report["benchmark"])
            self.assertFalse((root / "status.json").exists())
            self.assertFalse((root / "frame.jpg").exists())


def _jpeg() -> bytes:
    """Create a real tiny camera payload for the renderer integration test."""
    import cv2
    import numpy

    encoded, payload = cv2.imencode(".jpg", numpy.zeros((2, 2, 3), dtype=numpy.uint8))
    if not encoded:  # pragma: no cover - OpenCV host guard
        raise RuntimeError("test JPEG encoding failed")
    return bytes(payload)


_JPEG = _jpeg()


if __name__ == "__main__":
    unittest.main()
