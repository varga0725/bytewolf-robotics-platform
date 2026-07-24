"""The camera relay must write a real frame the dashboard can read, atomically.

No SITL here: the tests hand it a synthetic gz image message and check it decodes
a well-formed frame, writes a PNG plus a contract-shaped detections file, and
refuses a malformed message rather than publishing garbage.
"""

import base64
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.jpeg_encoder import is_jpeg
from brain.perception.png_encoder import encode_frame_to_png, is_png
from simulation.perception.camera_stream import (
    FULL_DOWN_CAMERA_TOPIC,
    FULL_FRONT_CAMERA_TOPIC,
    DetectionWorker,
    _PublishSchedule,
    camera_frame_from_gz_message,
    run_camera_stream,
    camera_topic,
    camera_frame_from_gz_image,
    publish_frame,
)


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
_RED = (220, 20, 20)


def _gz_image(width: int, height: int, red_box: tuple[int, int, int, int] | None = None) -> dict:
    pixels = bytearray((128, 128, 128) * (width * height))
    if red_box is not None:
        bx, by, bw, bh = red_box
        for v in range(by, by + bh):
            for u in range(bx, bx + bw):
                index = (v * width + u) * 3
                pixels[index], pixels[index + 1], pixels[index + 2] = _RED
    return {"width": width, "height": height, "data": base64.b64encode(bytes(pixels)).decode("ascii")}


def _detector() -> DetectorAdapter:
    return DetectorAdapter(ColourMarkerBackend(ColourTarget(*_RED), min_pixels=10, sample_step=1), source="test")


class FrameDecodeTests(unittest.TestCase):
    def test_selects_each_unique_full_sensor_camera_topic(self) -> None:
        self.assertEqual(camera_topic("front", full_sensors=True), FULL_FRONT_CAMERA_TOPIC)
        self.assertEqual(camera_topic("down", full_sensors=True), FULL_DOWN_CAMERA_TOPIC)

    def test_decodes_a_well_formed_gz_image(self) -> None:
        frame = camera_frame_from_gz_image(_gz_image(8, 6), sensor_id="down_rgb", captured_at=_NOW)

        self.assertIsNotNone(frame)
        self.assertEqual((frame.width, frame.height), (8, 6))
        self.assertTrue(frame.is_well_formed())

    def test_rejects_a_message_whose_data_does_not_match_dimensions(self) -> None:
        message = {"width": 100, "height": 100, "data": base64.b64encode(b"\x00" * 10).decode("ascii")}

        self.assertIsNone(camera_frame_from_gz_image(message, sensor_id="down_rgb", captured_at=_NOW))

    def test_rejects_a_message_missing_a_field(self) -> None:
        self.assertIsNone(camera_frame_from_gz_image({"width": 8}, sensor_id="down_rgb", captured_at=_NOW))


class PublishTests(unittest.TestCase):
    def test_writes_a_jpeg_frame_and_a_detections_file_by_default(self) -> None:
        frame = camera_frame_from_gz_image(
            _gz_image(60, 40, red_box=(20, 15, 12, 12)), sensor_id="down_rgb", captured_at=_NOW
        )
        with TemporaryDirectory() as directory:
            camera_path = Path(directory) / "camera.jpg"
            detections_path = Path(directory) / "detections.json"

            document = publish_frame(
                frame, camera_path=camera_path, detections_path=detections_path,
                detector=_detector(), now=lambda: _NOW,
            )

            self.assertTrue(is_jpeg(camera_path.read_bytes()))
            written = json.loads(detections_path.read_text(encoding="utf-8"))
            self.assertEqual(written["frame"], {"width": 60, "height": 40})
            self.assertEqual([d["label"] for d in document["detections"]], ["marker"])
            self.assertEqual(document["validity"], "valid")

    def test_can_write_lossless_png_when_asked(self) -> None:
        frame = camera_frame_from_gz_image(_gz_image(8, 6), sensor_id="down_rgb", captured_at=_NOW)
        with TemporaryDirectory() as directory:
            camera_path = Path(directory) / "camera.png"

            publish_frame(
                frame, camera_path=camera_path, detections_path=Path(directory) / "d.json",
                detector=_detector(), now=lambda: _NOW, encode=encode_frame_to_png,
            )

            self.assertTrue(is_png(camera_path.read_bytes()))

    def test_leaves_no_temporary_file_behind(self) -> None:
        frame = camera_frame_from_gz_image(_gz_image(8, 6), sensor_id="down_rgb", captured_at=_NOW)
        with TemporaryDirectory() as directory:
            camera_path = Path(directory) / "camera.png"
            detections_path = Path(directory) / "detections.json"

            publish_frame(
                frame, camera_path=camera_path, detections_path=detections_path,
                detector=_detector(), now=lambda: _NOW,
            )

            leftovers = [p.name for p in Path(directory).iterdir() if p.suffix == ".tmp"]
            self.assertEqual(leftovers, [])


if __name__ == "__main__":
    unittest.main()


class PublishScheduleTests(unittest.TestCase):
    """The cadence decides which frames reach the dashboard, so it has to be right."""

    def test_a_frame_arriving_a_hair_early_is_still_this_frames_turn(self) -> None:
        """Asking for exactly the rate the camera publishes at beats against it.

        Frames arrive every ~33.0 ms; a 33.3 ms deadline missed by a fraction
        dropped every other one, and 30 fps arriving became 19 fps published.
        """
        schedule = _PublishSchedule(period_s=1 / 30, detect_period_s=10.0)
        published = 0
        moment = 0.0
        for _ in range(90):
            if schedule.should_publish(moment):
                published += 1
            moment += 0.0330  # the camera's real interval, just inside the period

        self.assertGreater(published, 80, "nearly every frame must be published")

    def test_a_slower_cadence_still_drops_what_it_is_asked_to(self) -> None:
        schedule = _PublishSchedule(period_s=0.5, detect_period_s=10.0)
        published = sum(1 for index in range(90) if schedule.should_publish(index * 0.0333))

        self.assertLessEqual(published, 8, "a 0.5 s period must publish about twice a second")

    def test_a_late_frame_does_not_make_the_next_ones_catch_up(self) -> None:
        """Pacing from the missed deadline would publish a burst after a stall."""
        schedule = _PublishSchedule(period_s=0.1, detect_period_s=10.0)
        schedule.should_publish(0.0)

        self.assertTrue(schedule.should_publish(5.0), "the frame after a stall publishes")
        self.assertFalse(schedule.should_publish(5.01), "but the one right behind it does not")

    def test_detection_runs_on_its_own_slower_clock(self) -> None:
        schedule = _PublishSchedule(period_s=1 / 30, detect_period_s=0.2)
        detections = sum(1 for index in range(60) if schedule.should_detect(index * 0.0333))

        self.assertLessEqual(detections, 12)
        self.assertGreaterEqual(detections, 8)


class FrameDecodeTests(unittest.TestCase):
    def test_a_protobuf_image_becomes_an_rgb_frame(self) -> None:
        class Message:
            width, height = 4, 3
            data = b"\x10" * (4 * 3 * 3)

        frame = camera_frame_from_gz_message(Message(), sensor_id="front_rgb", captured_at=_NOW)

        assert frame is not None
        self.assertEqual((frame.width, frame.height), (4, 3))
        self.assertTrue(frame.is_well_formed())

    def test_a_frame_whose_payload_does_not_match_its_size_is_refused(self) -> None:
        class Message:
            width, height = 400, 300
            data = b"\x10" * 12

        self.assertIsNone(camera_frame_from_gz_message(Message(), sensor_id="front_rgb", captured_at=_NOW))


class RelayTests(unittest.TestCase):
    """The relay publishes what the subscription hands it, and stops when told."""

    def test_every_delivered_frame_is_written_at_the_default_cadence(self) -> None:
        frame = camera_frame_from_gz_message(
            type("M", (), {"width": 4, "height": 3, "data": b"\x20" * 36})(),
            sensor_id="front_rgb", captured_at=_NOW,
        )
        written: list[int] = []

        def fake_subscribe(_topic, *, sensor_id, now, on_frame):
            for _ in range(5):
                on_frame(frame)
            return object()

        with TemporaryDirectory() as directory:
            root = Path(directory)
            calls = iter([True, False])
            run_camera_stream(
                camera_topic="/camera",
                camera_path=root / "camera.jpg",
                detections_path=root / "detections.json",
                sensor_id="front_rgb",
                detector=DetectorAdapter(ColourMarkerBackend(ColourTarget(220, 20, 20, 70))),
                should_continue=lambda: next(calls, False),
                subscribe=fake_subscribe,
            )
            written.append((root / "camera.jpg").stat().st_size)

        self.assertGreater(written[0], 0, "the relay wrote a frame")


class DetectionWorkerTests(unittest.TestCase):
    def test_a_failing_detector_never_stops_the_picture(self) -> None:
        """Detections are the perishable half; the view must outlive them."""
        class Exploding:
            def analyze(self, _frame):
                raise RuntimeError("detector fell over")

        errors: list[BaseException] = []
        with TemporaryDirectory() as directory:
            worker = DetectionWorker(
                Exploding(), Path(directory) / "detections.json", on_error=errors.append
            ).start()
            frame = camera_frame_from_gz_message(
                type("M", (), {"width": 2, "height": 2, "data": b"\x00" * 12})(),
                sensor_id="front_rgb", captured_at=_NOW,
            )
            worker.submit(frame)
            for _ in range(100):
                if errors:
                    break
                time.sleep(0.01)
            worker.stop()

        self.assertTrue(errors, "the failure is reported rather than swallowed or fatal")
