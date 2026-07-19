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
import unittest

from brain.perception.colour_marker_backend import ColourMarkerBackend, ColourTarget
from brain.perception.detector import DetectorAdapter
from brain.perception.jpeg_encoder import is_jpeg
from brain.perception.png_encoder import encode_frame_to_png, is_png
from simulation.perception.camera_stream import (
    FULL_DOWN_CAMERA_TOPIC,
    FULL_FRONT_CAMERA_TOPIC,
    _stream_gz_images,
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

    def test_keeps_one_gazebo_subscription_open_for_multiple_frames(self) -> None:
        class Pipe:
            def __iter__(self):
                return iter((json.dumps(_gz_image(2, 1)) + "\n", "not json\n", json.dumps(_gz_image(1, 1)) + "\n"))

        class Process:
            stdout = Pipe()

            def __init__(self) -> None:
                self.terminated = False

            def poll(self):
                return None

            def terminate(self) -> None:
                self.terminated = True

            def wait(self, timeout: float) -> None:
                return None

        process = Process()
        messages = list(_stream_gz_images("/camera", {}, popen=lambda *_args, **_kwargs: process))

        self.assertEqual([message["width"] for message in messages], [2, 1])
        self.assertTrue(process.terminated)


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
