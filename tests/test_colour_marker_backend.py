"""The colour marker backend must find a real blob and read real pixels only.

It is the honest, dependency-free detector that anchors the estimator against a
real frame, so these tests build actual RGB buffers and check it locates the
marker's centre, ignores the background, and refuses an encoding it cannot read.
"""

from datetime import UTC, datetime
import unittest

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.colour_marker_backend import (
    ColourMarkerBackend,
    ColourMarkerBackendError,
    ColourTarget,
)
from brain.perception.detector import DetectorAdapter, DetectorState


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)
_GREY = (128, 128, 128)
_RED = (220, 20, 20)


def _image_with_red_square(width: int, height: int, box: tuple[int, int, int, int]) -> bytes:
    """A grey field with one red rectangle (x, y, w, h)."""
    bx, by, bw, bh = box
    pixels = bytearray(width * height * 3)
    for v in range(height):
        for u in range(width):
            colour = _RED if bx <= u < bx + bw and by <= v < by + bh else _GREY
            index = (v * width + u) * 3
            pixels[index], pixels[index + 1], pixels[index + 2] = colour
    return bytes(pixels)


def _frame(data: bytes, width: int, height: int, encoding: FrameEncoding = FrameEncoding.RGB8) -> CameraFrame:
    return CameraFrame(
        sensor_id="down_rgb", encoding=encoding, width=width, height=height,
        data=data, captured_at=_NOW, frame_id="down-1",
    )


class ColourMarkerBackendTests(unittest.TestCase):
    def test_finds_the_centre_of_a_red_marker(self) -> None:
        width, height = 80, 60
        data = _image_with_red_square(width, height, (30, 20, 20, 20))  # centre (40, 30)
        backend = ColourMarkerBackend(ColourTarget(*_RED), min_pixels=10, sample_step=1)

        detections = backend.detect(_frame(data, width, height))

        self.assertEqual(len(detections), 1)
        box = detections[0].bbox
        self.assertAlmostEqual(box.x + box.width / 2, 40.0, delta=1.5)
        self.assertAlmostEqual(box.y + box.height / 2, 30.0, delta=1.5)

    def test_reports_nothing_in_a_marker_free_field(self) -> None:
        width, height = 40, 30
        grey = bytes(_GREY * (width * height))
        backend = ColourMarkerBackend(ColourTarget(*_RED), min_pixels=10, sample_step=1)

        self.assertEqual(backend.detect(_frame(grey, width, height)), ())

    def test_refuses_a_non_rgb_encoding(self) -> None:
        backend = ColourMarkerBackend(ColourTarget(*_RED))

        with self.assertRaisesRegex(ColourMarkerBackendError, "RGB8 only"):
            backend.detect(_frame(b"\x00" * (8 * 6 * 2), 8, 6, encoding=FrameEncoding.DEPTH16))

    def test_drives_the_detector_adapter_end_to_end(self) -> None:
        """A real frame through the real backend yields a valid detection result."""
        width, height = 80, 60
        data = _image_with_red_square(width, height, (30, 20, 20, 20))
        adapter = DetectorAdapter(
            ColourMarkerBackend(ColourTarget(*_RED), min_pixels=10, sample_step=1), source="down + colour"
        )

        result = adapter.analyze(_frame(data, width, height))

        self.assertEqual(result.state(_NOW), DetectorState.VALID)
        self.assertEqual([d.label for d in result.usable_detections(_NOW)], ["marker"])

    def test_a_backend_error_fails_closed_through_the_adapter(self) -> None:
        adapter = DetectorAdapter(ColourMarkerBackend(ColourTarget(*_RED)))

        result = adapter.analyze(_frame(b"\x00" * (8 * 6 * 2), 8, 6, encoding=FrameEncoding.DEPTH16))

        self.assertEqual(result.state(_NOW), DetectorState.INVALID)


if __name__ == "__main__":
    unittest.main()
