"""The PNG encoder must be lossless: what the dashboard shows is what was seen.

There is no image library here, so the tests decode the encoder's own output --
parse the chunks, inflate IDAT, strip the per-row filter byte -- and check the
pixels come back exactly. A lossy or scrambled encode would fail that round-trip.
"""

from datetime import UTC, datetime
import struct
import unittest
import zlib

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.png_encoder import (
    PngEncodeError,
    encode_frame_to_png,
    encode_rgb8_to_png,
    is_png,
)


def _decode_png(png: bytes) -> tuple[int, int, bytes]:
    """Minimal PNG reader for filter-0 truecolour, for verifying the encoder."""
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    width = height = 0
    idat = bytearray()
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset : offset + 4])
        kind = png[offset + 4 : offset + 8]
        payload = png[offset + 8 : offset + 8 + length]
        if kind == b"IHDR":
            width, height = struct.unpack(">II", payload[:8])
        elif kind == b"IDAT":
            idat.extend(payload)
        offset += 12 + length
    raw = zlib.decompress(bytes(idat))
    stride = width * 3
    pixels = bytearray()
    for row in range(height):
        start = row * (stride + 1)
        assert raw[start] == 0, "expected filter type 0"
        pixels.extend(raw[start + 1 : start + 1 + stride])
    return width, height, bytes(pixels)


class PngEncoderTests(unittest.TestCase):
    def test_a_known_image_round_trips_pixel_for_pixel(self) -> None:
        width, height = 3, 2
        # r, g, b per pixel across a 3x2 image
        data = bytes([255, 0, 0, 0, 255, 0, 0, 0, 255, 10, 20, 30, 40, 50, 60, 70, 80, 90])

        png = encode_rgb8_to_png(data, width, height)

        self.assertTrue(is_png(png))
        self.assertEqual(_decode_png(png), (width, height, data))

    def test_a_larger_image_round_trips(self) -> None:
        width, height = 64, 48
        data = bytes((i * 7) % 256 for i in range(width * height * 3))

        self.assertEqual(_decode_png(encode_rgb8_to_png(data, width, height)), (width, height, data))

    def test_encodes_a_camera_frame(self) -> None:
        data = bytes(4 * 2 * 3)
        frame = CameraFrame(
            sensor_id="down_rgb", encoding=FrameEncoding.RGB8, width=4, height=2,
            data=data, captured_at=datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
        )

        png = encode_frame_to_png(frame)

        self.assertEqual(_decode_png(png), (4, 2, data))

    def test_refuses_a_length_that_does_not_match_dimensions(self) -> None:
        with self.assertRaisesRegex(PngEncodeError, "does not match"):
            encode_rgb8_to_png(b"\x00" * 10, 4, 2)

    def test_refuses_a_non_rgb_frame(self) -> None:
        frame = CameraFrame(
            sensor_id="depth", encoding=FrameEncoding.DEPTH16, width=4, height=2,
            data=b"\x00" * 16, captured_at=datetime(2026, 7, 18, 9, 0, tzinfo=UTC),
        )
        with self.assertRaisesRegex(PngEncodeError, "RGB8"):
            encode_frame_to_png(frame)


if __name__ == "__main__":
    unittest.main()
