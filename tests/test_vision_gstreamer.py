"""GStreamer appsink adapter tests without requiring GStreamer bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import unittest

from brain.vision.gstreamer import (
    GStreamerIngestAdapter,
    GStreamerIngestError,
    GStreamerStreamState,
    StreamBinding,
)


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class FakeBuffer:
    data: bytes
    captured_at: datetime = NOW - timedelta(milliseconds=12)
    mime_type: str = "image/jpeg"
    width_px: int = 1280
    height_px: int = 720

    def payload_bytes(self) -> bytes:
        return self.data


class FakePipeline:
    def __init__(self, binding: StreamBinding, buffers: list[FakeBuffer | None] | None = None) -> None:
        self.binding = binding
        self.buffers = list(buffers or [])
        self.raise_on_pull: Exception | None = None

    def pull_buffer(self) -> FakeBuffer | None:
        if self.raise_on_pull is not None:
            raise self.raise_on_pull
        return self.buffers.pop(0) if self.buffers else None


class GStreamerIngestAdapterTests(unittest.TestCase):
    def make_adapter(self, pipeline: FakePipeline, **kwargs: object) -> GStreamerIngestAdapter:
        return GStreamerIngestAdapter(
            pipeline,
            binding=StreamBinding("edge-1", "front-rgb", "session-a"),
            calibration_version="cal-v3",
            clock=lambda: NOW,
            **kwargs,
        )

    def test_builds_hashed_contract_frame_from_injected_appsink_buffer(self) -> None:
        payload = b"jpeg frame"
        pipeline = FakePipeline(StreamBinding("edge-1", "front-rgb", "session-a"), [FakeBuffer(payload)])

        frame = self.make_adapter(pipeline).poll()

        self.assertEqual(frame.device_id, "edge-1")  # type: ignore[union-attr]
        self.assertEqual(frame.camera_id, "front-rgb")  # type: ignore[union-attr]
        self.assertEqual(frame.stream_session_id, "session-a")  # type: ignore[union-attr]
        self.assertEqual(frame.frame_sequence, 0)  # type: ignore[union-attr]
        self.assertEqual(frame.encoding, "jpeg")  # type: ignore[union-attr]
        self.assertEqual(frame.payload_hash, hashlib.sha256(payload).hexdigest())  # type: ignore[union-attr]
        self.assertEqual(frame.latency_ms, 12.0)  # type: ignore[union-attr]

    def test_rejects_pipeline_identity_mismatch_before_emitting_frame(self) -> None:
        pipeline = FakePipeline(StreamBinding("edge-2", "front-rgb", "session-a"), [FakeBuffer(b"frame")])
        adapter = self.make_adapter(pipeline)

        with self.assertRaisesRegex(GStreamerIngestError, "binding"):
            adapter.poll()

        self.assertEqual(adapter.stream_state, GStreamerStreamState.UNAVAILABLE)

    def test_rejects_unsupported_mime_type_and_does_not_advance_sequence(self) -> None:
        pipeline = FakePipeline(
            StreamBinding("edge-1", "front-rgb", "session-a"),
            [FakeBuffer(b"bad", mime_type="image/png"), FakeBuffer(b"good")],
        )
        adapter = self.make_adapter(pipeline)

        with self.assertRaisesRegex(GStreamerIngestError, "MIME"):
            adapter.poll()
        adapter.reconnect("session-b")
        pipeline.binding = StreamBinding("edge-1", "front-rgb", "session-b")
        frame = adapter.poll()

        self.assertEqual(frame.frame_sequence, 0)  # type: ignore[union-attr]

    def test_disconnect_and_reconnect_rotate_session_and_reset_sequence(self) -> None:
        pipeline = FakePipeline(StreamBinding("edge-1", "front-rgb", "session-a"), [FakeBuffer(b"one")])
        adapter = self.make_adapter(pipeline)
        self.assertEqual(adapter.poll().frame_sequence, 0)  # type: ignore[union-attr]

        adapter.disconnect("network lost")
        self.assertEqual(adapter.stream_state, GStreamerStreamState.UNAVAILABLE)
        with self.assertRaisesRegex(GStreamerIngestError, "unavailable"):
            adapter.poll()
        pipeline.binding = StreamBinding("edge-1", "front-rgb", "session-b")
        pipeline.buffers.append(FakeBuffer(b"two"))
        adapter.reconnect("session-b")

        frame = adapter.poll()

        self.assertEqual(frame.stream_session_id, "session-b")  # type: ignore[union-attr]
        self.assertEqual(frame.frame_sequence, 0)  # type: ignore[union-attr]
        self.assertEqual(adapter.stream_state, GStreamerStreamState.HEALTHY)

    def test_pull_error_disconnects_and_surfaces_reconnect_required(self) -> None:
        pipeline = FakePipeline(StreamBinding("edge-1", "front-rgb", "session-a"))
        pipeline.raise_on_pull = RuntimeError("appsink closed")
        adapter = self.make_adapter(pipeline)

        with self.assertRaisesRegex(GStreamerIngestError, "appsink closed"):
            adapter.poll()

        self.assertEqual(adapter.stream_state, GStreamerStreamState.UNAVAILABLE)
        self.assertIn("appsink closed", adapter.last_error)

    def test_dropped_frames_are_monotonic_and_embedded_in_each_frame(self) -> None:
        pipeline = FakePipeline(StreamBinding("edge-1", "front-rgb", "session-a"), [FakeBuffer(b"frame")])
        adapter = self.make_adapter(pipeline)
        adapter.record_dropped_frames(3)

        frame = adapter.poll()

        self.assertEqual(frame.dropped_frames, 3)  # type: ignore[union-attr]
        with self.assertRaises(ValueError):
            adapter.record_dropped_frames(-1)


if __name__ == "__main__":
    unittest.main()
