"""JPEG encoding for the live stream: valid, well-sized, and RGB8-only.

JPEG is lossy, so the pixels are not asserted byte-for-byte; the tests check that
the output is a real JPEG of the right dimensions and is much smaller than the
raw frame, which is the whole reason to use it for a 1080p live view.
"""

from datetime import UTC, datetime
from io import BytesIO
import unittest

from PIL import Image

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.jpeg_encoder import (
    JpegEncodeError,
    encode_frame_to_jpeg,
    encode_rgb8_to_jpeg,
    is_jpeg,
)


class JpegEncoderTests(unittest.TestCase):
    def test_encodes_a_valid_jpeg_of_the_right_size(self) -> None:
        width, height = 64, 48
        data = bytes((i * 5) % 256 for i in range(width * height * 3))

        jpeg = encode_rgb8_to_jpeg(data, width, height)

        self.assertTrue(is_jpeg(jpeg))
        with Image.open(BytesIO(jpeg)) as image:
            self.assertEqual(image.size, (width, height))
            self.assertEqual(image.mode, "RGB")

    def test_is_much_smaller_than_the_raw_frame(self) -> None:
        # A flat image compresses hard; the point is JPEG is far below the raw size.
        width, height = 320, 240
        data = bytes([128, 128, 128]) * (width * height)

        jpeg = encode_rgb8_to_jpeg(data, width, height)

        self.assertLess(len(jpeg), width * height * 3 // 10)

    def test_refuses_a_length_that_does_not_match_dimensions(self) -> None:
        with self.assertRaisesRegex(JpegEncodeError, "does not match"):
            encode_rgb8_to_jpeg(b"\x00" * 10, 4, 2)

    def test_refuses_an_out_of_range_quality(self) -> None:
        with self.assertRaisesRegex(JpegEncodeError, "quality"):
            encode_rgb8_to_jpeg(b"\x00" * 24, 4, 2, quality=0)

    def test_refuses_a_non_rgb_frame(self) -> None:
        frame = CameraFrame(
            sensor_id="depth", encoding=FrameEncoding.DEPTH16, width=4, height=2,
            data=b"\x00" * 16, captured_at=datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
        )
        with self.assertRaisesRegex(JpegEncodeError, "RGB8"):
            encode_frame_to_jpeg(frame)


if __name__ == "__main__":
    unittest.main()
