"""Tests for the optional, injected Gazebo RGB ingest boundary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest

from brain.vision.contracts import ResultState
from brain.vision.gazebo import GazeboImageIngestAdapter, GazeboImageValidationError


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class FakeBindings:
    def __init__(self) -> None:
        self.topic: str | None = None
        self.callback = None
        self.unsubscribe_calls = 0

    def subscribe(self, topic: str, callback: object):
        self.topic = topic
        self.callback = callback

        def unsubscribe() -> None:
            self.unsubscribe_calls += 1

        return unsubscribe

    def publish(self, message: object) -> None:
        assert self.callback is not None
        self.callback(message)


def image(*, data: bytes = b"\x01\x02\x03" * 2, stamp: datetime = NOW, **overrides: object) -> object:
    values: dict[str, object] = {
        "width": 2,
        "height": 1,
        "step": 6,
        "pixel_format": "RGB_INT8",
        "data": data,
        "header": SimpleNamespace(stamp=SimpleNamespace(sec=stamp.timestamp().__int__(), nsec=stamp.microsecond * 1000)),
    }
    return SimpleNamespace(**{**values, **overrides})


class GazeboImageIngestAdapterTests(unittest.TestCase):
    def make_adapter(self, bindings: FakeBindings, **overrides: object) -> GazeboImageIngestAdapter:
        values: dict[str, object] = {
            "bindings": bindings,
            "topic": "/world/test/model/x500/link/front/sensor/camera/image",
            "device_id": "gazebo-x500-01",
            "camera_id": "front-rgb",
            "calibration_version": "sim-v1",
            "clock": lambda: NOW,
            "session_id_factory": lambda: "session-1",
        }
        return GazeboImageIngestAdapter(**{**values, **overrides})  # type: ignore[arg-type]

    def test_constructs_contract_bound_rgb_frame_from_injected_subscription(self) -> None:
        bindings = FakeBindings()
        adapter = self.make_adapter(bindings)

        adapter.start()
        bindings.publish(image())
        frame = adapter.poll()

        self.assertEqual(bindings.topic, "/world/test/model/x500/link/front/sensor/camera/image")
        self.assertIsNotNone(frame)
        assert frame is not None
        self.assertEqual(frame.device_id, "gazebo-x500-01")
        self.assertEqual(frame.camera_id, "front-rgb")
        self.assertEqual(frame.stream_session_id, "session-1")
        self.assertEqual(frame.frame_sequence, 0)
        self.assertEqual(frame.encoding, "rgb8")
        self.assertEqual(frame.width_px, 2)
        self.assertEqual(frame.height_px, 1)
        self.assertEqual(frame.payload_hash, "3ba35ee83f218013f8b83f08ae2c06c9d8e05a198d271c57c9e9a1771b44a211")

    def test_rejects_non_rgb_malformed_payload_and_never_emits_a_frame(self) -> None:
        bindings = FakeBindings()
        adapter = self.make_adapter(bindings)
        adapter.start()

        bindings.publish(image(pixel_format="BGR_INT8"))

        self.assertIsNone(adapter.poll())
        lifecycle = adapter.lifecycle(NOW)
        self.assertEqual(lifecycle.stream_state, "degraded")
        self.assertIn("pixel_format", lifecycle.reason)
        self.assertEqual(adapter.health(NOW).state(NOW), ResultState.MISSING)

    def test_rejects_payload_that_does_not_exactly_match_rgb_dimensions_and_step(self) -> None:
        bindings = FakeBindings()
        adapter = self.make_adapter(bindings)
        adapter.start()

        bindings.publish(image(data=b"\x00" * 5))

        self.assertIsNone(adapter.poll())
        self.assertIn("payload length", adapter.lifecycle(NOW).reason)

    def test_rejects_future_capture_timestamp_without_advancing_sequence(self) -> None:
        bindings = FakeBindings()
        adapter = self.make_adapter(bindings, max_clock_skew=timedelta(milliseconds=100))
        adapter.start()
        bindings.publish(image(stamp=NOW + timedelta(seconds=1)))
        bindings.publish(image(data=b"\x04\x05\x06" * 2))

        frame = adapter.poll()

        assert frame is not None
        self.assertEqual(frame.frame_sequence, 0)
        self.assertEqual(adapter.lifecycle(NOW).rejected_messages, 1)

    def test_newest_callback_frame_wins_and_reports_drop(self) -> None:
        bindings = FakeBindings()
        adapter = self.make_adapter(bindings)
        adapter.start()
        bindings.publish(image())
        bindings.publish(image(data=b"\x04\x05\x06" * 2))

        frame = adapter.poll()

        assert frame is not None
        self.assertEqual(frame.frame_sequence, 1)
        self.assertEqual(frame.dropped_frames, 1)
        self.assertEqual(adapter.health(NOW).dropped_frames, 1)

    def test_disconnect_and_reconnect_create_new_session_and_report_lifecycle(self) -> None:
        bindings = FakeBindings()
        sessions = iter(("session-1", "session-2"))
        adapter = self.make_adapter(bindings, session_id_factory=lambda: next(sessions))
        adapter.start()
        adapter.disconnect("transport lost", NOW)

        self.assertEqual(adapter.lifecycle(NOW).stream_state, "unavailable")
        adapter.reconnect(NOW)
        bindings.publish(image())
        frame = adapter.poll()

        assert frame is not None
        self.assertEqual(frame.stream_session_id, "session-2")
        self.assertEqual(frame.frame_sequence, 0)
        self.assertEqual(adapter.lifecycle(NOW).reconnect_count, 1)
        self.assertEqual(adapter.lifecycle(NOW).stream_state, "healthy")

    def test_start_rejects_invalid_binding_result_fail_closed(self) -> None:
        class InvalidBindings:
            def subscribe(self, topic: str, callback: object) -> object:
                return object()

        adapter = GazeboImageIngestAdapter(
            bindings=InvalidBindings(), topic="/camera", device_id="sim", camera_id="front",
            calibration_version="v1", clock=lambda: NOW, session_id_factory=lambda: "session",
        )

        with self.assertRaises(GazeboImageValidationError):
            adapter.start()
        self.assertEqual(adapter.lifecycle(NOW).stream_state, "unavailable")


if __name__ == "__main__":
    unittest.main()
