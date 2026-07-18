"""The frame contract must stay multi-sensor, multi-format, and self-guarding.

A malformed raw frame reaching a detector is how a perception pipeline starts
seeing things that are not there, so the contract's job is to catch that at the
door -- and to treat every encoding, not just one, as a first-class citizen.
"""

from datetime import UTC, datetime
import unittest

from brain.perception.camera_frame import (
    CameraFrame,
    CameraFrameError,
    FrameEncoding,
)


_NOW = datetime(2026, 7, 18, 9, 0, 0, tzinfo=UTC)


def _frame(encoding: FrameEncoding, data: bytes, *, width: int = 4, height: int = 2, sensor_id: str = "front_rgb") -> CameraFrame:
    return CameraFrame(
        sensor_id=sensor_id, encoding=encoding, width=width, height=height,
        data=data, captured_at=_NOW,
    )


class EncodingTaxonomyTests(unittest.TestCase):
    def test_raw_colour_encodings_declare_a_fixed_stride(self) -> None:
        self.assertEqual(FrameEncoding.RGB8.bytes_per_pixel, 3)
        self.assertEqual(FrameEncoding.MONO8.bytes_per_pixel, 1)
        self.assertEqual(FrameEncoding.YUV422.bytes_per_pixel, 2)
        self.assertTrue(FrameEncoding.RGB8.is_raw)

    def test_depth_encodings_are_recognised_as_depth(self) -> None:
        self.assertTrue(FrameEncoding.DEPTH16.is_depth)
        self.assertEqual(FrameEncoding.DEPTH16.bytes_per_pixel, 2)
        self.assertEqual(FrameEncoding.DEPTH32F.bytes_per_pixel, 4)
        self.assertFalse(FrameEncoding.DEPTH16.is_raw)

    def test_a_compressed_encoding_has_no_fixed_stride(self) -> None:
        self.assertIsNone(FrameEncoding.JPEG.bytes_per_pixel)
        self.assertTrue(FrameEncoding.JPEG.is_compressed)


class WellFormedTests(unittest.TestCase):
    def test_a_raw_frame_with_the_exact_byte_count_is_well_formed(self) -> None:
        frame = _frame(FrameEncoding.RGB8, b"\x00" * (4 * 2 * 3))

        self.assertEqual(frame.expected_raw_byte_count, 24)
        self.assertTrue(frame.is_well_formed())

    def test_a_raw_frame_with_the_wrong_byte_count_is_refused(self) -> None:
        """A short buffer for the claimed dimensions is not a picture of anything."""
        frame = _frame(FrameEncoding.RGB8, b"\x00" * 10)

        self.assertFalse(frame.is_well_formed())

    def test_a_depth_frame_is_sized_by_its_own_stride(self) -> None:
        frame = _frame(FrameEncoding.DEPTH16, b"\x00" * (4 * 2 * 2))

        self.assertTrue(frame.is_well_formed())

    def test_a_compressed_frame_is_well_formed_on_any_non_empty_bytes(self) -> None:
        frame = _frame(FrameEncoding.JPEG, b"\xff\xd8\xff\xd9")

        self.assertIsNone(frame.expected_raw_byte_count)
        self.assertTrue(frame.is_well_formed())

    def test_an_empty_frame_is_never_well_formed(self) -> None:
        self.assertFalse(_frame(FrameEncoding.JPEG, b"").is_well_formed())
        self.assertFalse(_frame(FrameEncoding.RGB8, b"").is_well_formed())


class ConstructionGuardTests(unittest.TestCase):
    def test_a_frame_must_name_its_sensor(self) -> None:
        with self.assertRaisesRegex(CameraFrameError, "name the sensor"):
            _frame(FrameEncoding.RGB8, b"\x00" * 24, sensor_id="")

    def test_a_frame_must_have_positive_dimensions(self) -> None:
        with self.assertRaisesRegex(CameraFrameError, "positive dimensions"):
            _frame(FrameEncoding.RGB8, b"\x00", width=0)

    def test_a_frame_capture_time_must_be_timezone_aware(self) -> None:
        with self.assertRaisesRegex(CameraFrameError, "timezone-aware"):
            CameraFrame(
                sensor_id="front_rgb", encoding=FrameEncoding.RGB8, width=4, height=2,
                data=b"\x00" * 24, captured_at=datetime(2026, 7, 18, 9, 0, 0),
            )

    def test_the_same_shape_serves_different_sensors(self) -> None:
        """One contract, many sensors: front rgb, down rgb, and depth all fit."""
        front = _frame(FrameEncoding.RGB8, b"\x00" * 24, sensor_id="front_rgb")
        down = _frame(FrameEncoding.RGB8, b"\x00" * 24, sensor_id="down_rgb")
        depth = _frame(FrameEncoding.DEPTH16, b"\x00" * 16, sensor_id="depth")

        self.assertEqual({front.sensor_id, down.sensor_id, depth.sensor_id}, {"front_rgb", "down_rgb", "depth"})
        self.assertTrue(all(f.is_well_formed() for f in (front, down, depth)))


if __name__ == "__main__":
    unittest.main()
