"""Live Gazebo CLI orchestration tests without Gazebo or model dependencies."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest

from brain.cli.vision_gazebo_pipeline import HashVerifiedGazeboSource, run_gazebo_pipeline
from brain.vision.contracts import BoundingBox, Detection


_NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


class Stamp:
    def __init__(self, captured_at: datetime) -> None:
        self.sec = int(captured_at.timestamp())
        self.nsec = captured_at.microsecond * 1_000


class Header:
    def __init__(self, captured_at: datetime) -> None:
        self.stamp = Stamp(captured_at)


class Image:
    def __init__(self, payload: bytes, *, captured_at: datetime = _NOW, step: int = 6) -> None:
        self.width = 2
        self.height = 2
        self.step = step
        self.pixel_format = "RGB_INT8"
        self.data = payload
        self.header = Header(captured_at)


class FakeBindings:
    def __init__(self) -> None:
        self.callback = None
        self.subscriptions = 0

    def subscribe(self, _topic, callback):  # type: ignore[no-untyped-def]
        self.callback = callback
        self.subscriptions += 1
        return lambda: None

    def emit(self, image: object) -> None:
        assert self.callback is not None
        self.callback(image)


class FakeDetector:
    model_id = "fake-yolo"
    model_version = "test-v1"

    def __init__(self) -> None:
        self.hashes: list[str] = []

    def detect(self, frame, _now):  # type: ignore[no-untyped-def]
        self.hashes.append(frame.payload_hash)
        return (Detection("person", 0.9, BoundingBox(0, 0, 1, 1)),)


class GazeboVisionPipelineTests(unittest.TestCase):
    def make_source(self, bindings: FakeBindings, *, session_ids: tuple[str, ...] = ("session-a", "session-b")) -> HashVerifiedGazeboSource:
        iterator = iter(session_ids)
        source = HashVerifiedGazeboSource(
            bindings=bindings,
            topic="/camera/front/image",
            device_id="gazebo-x500-01",
            camera_id="front-1",
            calibration_version="cal-v1",
            clock=lambda: _NOW,
            session_id_factory=lambda: next(iterator),
        )
        source.start()
        return source

    def test_rgb_frame_is_hash_bound_detected_tracked_overlaid_and_published(self) -> None:
        bindings = FakeBindings()
        source = self.make_source(bindings)
        payload = bytes(range(12))
        bindings.emit(Image(payload))
        detector = FakeDetector()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_gazebo_pipeline(
                source, detector, status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=1,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["processed_frames"], 1)
            self.assertEqual(status["detections"][0]["tracker_id"], "local-000001")
            self.assertEqual((root / "frame.jpg").read_bytes()[:2], b"\xff\xd8")
            self.assertEqual(source.resolve(detector.hashes[0]), payload)

    def test_malformed_image_fails_closed_without_fake_detection(self) -> None:
        bindings = FakeBindings()
        source = self.make_source(bindings)
        bindings.emit(Image(b"too short"))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_gazebo_pipeline(
                source, FakeDetector(), status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=lambda: _NOW, sleep=lambda _seconds: None, max_iterations=1,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["processed_frames"], 0)
            self.assertEqual(report["unavailable_frames"], 1)
            self.assertEqual(status["stream_state"], "unavailable")
            # The published frame is a neutral unavailable marker, not a
            # fabricated camera observation; the status remains fail-closed.
            self.assertEqual((root / "frame.jpg").read_bytes()[:2], b"\xff\xd8")

    def test_recovery_rotates_session_and_reports_latency_and_drops(self) -> None:
        bindings = FakeBindings()
        source = self.make_source(bindings)
        bindings.emit(Image(bytes(range(12)), captured_at=_NOW - timedelta(milliseconds=100)))
        call_count = 0

        def now() -> datetime:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                bindings.emit(Image(b"broken"))
            elif call_count == 4:
                bindings.emit(Image(bytes(range(12)), captured_at=_NOW - timedelta(milliseconds=300)))
                bindings.emit(Image(bytes(range(12))))
            return _NOW

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run_gazebo_pipeline(
                source, FakeDetector(), status_path=root / "status.json", frame_path=root / "frame.jpg",
                now=now, sleep=lambda _seconds: None, max_iterations=4, max_reconnects=1,
            )
            status = json.loads((root / "status.json").read_text(encoding="utf-8"))
            self.assertEqual(report["processed_frames"], 2)
            self.assertEqual(report["unavailable_frames"], 1)
            self.assertEqual(report["reconnects"], 1)
            self.assertEqual(report["stream_session_id"], "session-b")
            self.assertEqual(report["benchmark"], {
                "sample_count": 2,
                "p50_latency_ms": 50.0,
                "p95_latency_ms": 100.0,
                "dropped_frames": 1,
            })
            self.assertEqual(status["stream_state"], "healthy")

    def test_reconnect_rejects_a_non_rotated_session(self) -> None:
        bindings = FakeBindings()
        source = self.make_source(bindings, session_ids=("session-a", "session-a"))
        with self.assertRaisesRegex(ValueError, "rotate"):
            source.reconnect(_NOW)


if __name__ == "__main__":
    unittest.main()
