"""Runtime orchestration stays dependency-free and observation-only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.vision.contracts import CAMERA_FRAME_V1, BoundingBox, CameraFrame, Detection, ResultState
from brain.vision.runtime import (
    DeterministicDetector,
    GazeboIngestAdapter,
    GStreamerIngestAdapter,
    RuntimeState,
    VisionRuntime,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


def frame(sequence: int, **overrides: object) -> CameraFrame:
    values: dict[str, object] = {
        "contract_version": CAMERA_FRAME_V1,
        "device_id": "sim-01", "camera_id": "front-rgb", "stream_session_id": "run-1",
        "frame_sequence": sequence, "captured_at": NOW - timedelta(milliseconds=5),
        "received_at": NOW, "calibration_version": "v1", "payload_hash": "a" * 64,
        "encoding": "jpeg", "width_px": 1280, "height_px": 720, "latency_ms": 5.0,
        "dropped_frames": 0,
    }
    return CameraFrame(**{**values, **overrides})  # type: ignore[arg-type]


class VisionRuntimeTests(unittest.TestCase):
    def test_newest_frame_wins_and_reports_backpressure_drop(self) -> None:
        runtime = VisionRuntime(DeterministicDetector())
        runtime.submit(frame(1))
        runtime.submit(frame(2, payload_hash="b" * 64))

        outcome = runtime.process_next(NOW)

        self.assertEqual(outcome.state, RuntimeState.PROCESSED)
        self.assertEqual(outcome.frame.frame_sequence, 2)  # type: ignore[union-attr]
        self.assertEqual(outcome.health.dropped_frames, 1)
        self.assertEqual(outcome.health.backlog_frames, 0)

    def test_invalid_input_does_not_reach_detector_and_is_not_no_detection(self) -> None:
        detector = DeterministicDetector((Detection("person", .9, BoundingBox(0, 0, 10, 10)),))
        runtime = VisionRuntime(detector)
        runtime.submit(frame(1, payload_hash="bad"))

        outcome = runtime.process_next(NOW)

        self.assertEqual(outcome.state, RuntimeState.REJECTED)
        self.assertEqual(outcome.validation.state, ResultState.INVALID)
        self.assertIsNone(outcome.detection)

    def test_stream_disconnect_is_visible_and_reconnect_accepts_new_session(self) -> None:
        runtime = VisionRuntime(DeterministicDetector())
        runtime.disconnect("network lost", NOW)
        self.assertEqual(runtime.health(NOW).stream_state, "unavailable")
        runtime.reconnect(NOW)
        runtime.submit(frame(0, stream_session_id="run-2", payload_hash="b" * 64))

        outcome = runtime.process_next(NOW)

        self.assertEqual(outcome.state, RuntimeState.PROCESSED)
        self.assertEqual(runtime.health(NOW).stream_state, "healthy")

    def test_gazebo_and_gstreamer_boundaries_only_invoke_injected_source(self) -> None:
        source = iter((frame(1),))
        for adapter in (GazeboIngestAdapter(lambda: next(source, None)), GStreamerIngestAdapter(lambda: None)):
            self.assertIsInstance(adapter.poll(), (CameraFrame, type(None)))


if __name__ == "__main__":
    unittest.main()
