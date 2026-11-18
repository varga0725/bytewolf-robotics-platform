from datetime import UTC, datetime
import math
import struct
import unittest

from brain.perception.camera_frame import CameraFrame, FrameEncoding
from brain.perception.depth_obstacle import DepthObstacleError, depth_obstacle_observation
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 28, 12, 0, tzinfo=UTC)


def _frame(values: list[float], *, encoding: FrameEncoding = FrameEncoding.DEPTH32F) -> CameraFrame:
    data = struct.pack("<" + "f" * len(values), *values) if encoding is FrameEncoding.DEPTH32F else struct.pack("<" + "H" * len(values), *(round(value * 1000) for value in values))
    return CameraFrame("depth-front", encoding, 3, 1, data, NOW)


class DepthObstacleTests(unittest.TestCase):
    def test_depth_frame_emits_a_contract_valid_nearest_obstacle(self) -> None:
        document = depth_obstacle_observation(
            _frame([30.0, 2.5, 30.0]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        observation = load_observation(document)
        centre = next(item for item in document["payload"]["sectors"] if item["yaw_deg"] == 0.0)
        self.assertEqual(observation.kind, "obstacle")
        self.assertEqual(centre["coverage"], "measured")
        self.assertEqual(centre["distance_m"], 2.5)

    def test_invalid_depth_does_not_become_clear_space(self) -> None:
        document = depth_obstacle_observation(
            _frame([0.0, 0.0, 0.0]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        self.assertTrue(all(item["coverage"] == "unobserved" for item in document["payload"]["sectors"]))

    def test_nan_depth_does_not_become_clear_space(self) -> None:
        document = depth_obstacle_observation(
            _frame([math.nan, math.nan, math.nan]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        load_observation(document)
        self.assertTrue(all(item["coverage"] == "unobserved" for item in document["payload"]["sectors"]))

    def test_below_sensor_minimum_is_unobserved_not_clear(self) -> None:
        document = depth_obstacle_observation(
            _frame([0.1, 0.1, 0.1]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        load_observation(document)
        centre = next(item for item in document["payload"]["sectors"] if item["yaw_deg"] == 0.0)
        self.assertEqual(centre["coverage"], "unobserved")
        self.assertNotIn("distance_m", centre)

    def test_below_minimum_mixed_with_return_reports_min_range_not_farther_return(self) -> None:
        for width, height in ((1, 2), (2, 1)):
            values = [0.1, 1.5]
            frame = CameraFrame("depth-front", FrameEncoding.DEPTH32F, width, height, struct.pack("<ff", *values), NOW)
            document = depth_obstacle_observation(
                frame, vehicle_id="x500", horizontal_fov_deg=30,
                min_range_m=0.2, max_range_m=30, sector_width_deg=30,
            )
            load_observation(document)
            (sector,) = document["payload"]["sectors"]
            self.assertEqual(sector["coverage"], "measured")
            self.assertEqual(sector["distance_m"], 0.2)

    def test_invalid_with_beyond_max_is_unobserved_not_clear(self) -> None:
        for values in ((math.nan, 40.0), (0.0, 40.0)):
            with self.subTest(values=values):
                frame = CameraFrame(
                    "depth-front", FrameEncoding.DEPTH32F, 1, 2,
                    struct.pack("<ff", *values), NOW,
                )
                document = depth_obstacle_observation(
                    frame, vehicle_id="x500", horizontal_fov_deg=30,
                    min_range_m=0.2, max_range_m=30, sector_width_deg=30,
                )
                (sector,) = document["payload"]["sectors"]
                self.assertEqual(sector["coverage"], "unobserved")

    def test_depth16_saturation_is_unobserved(self) -> None:
        frame = CameraFrame(
            "depth-front", FrameEncoding.DEPTH16, 1, 1, struct.pack("<H", 65535), NOW,
        )
        document = depth_obstacle_observation(
            frame, vehicle_id="x500", horizontal_fov_deg=30,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        (sector,) = document["payload"]["sectors"]
        self.assertEqual(sector["coverage"], "unobserved")

    def test_exactly_max_range_stays_clear(self) -> None:
        document = depth_obstacle_observation(
            _frame([30.0, 30.0, 30.0]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        centre = next(item for item in document["payload"]["sectors"] if item["yaw_deg"] == 0.0)
        self.assertEqual(centre["coverage"], "clear")

    def test_in_range_only_keeps_measured_clear_behavior(self) -> None:
        measured = depth_obstacle_observation(
            _frame([2.0, 2.0, 2.0]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        load_observation(measured)
        centre = next(item for item in measured["payload"]["sectors"] if item["yaw_deg"] == 0.0)
        self.assertEqual(centre["coverage"], "measured")
        self.assertEqual(centre["distance_m"], 2.0)

        clear = depth_obstacle_observation(
            _frame([30.0, 30.0, 30.0]), vehicle_id="x500", horizontal_fov_deg=90,
            min_range_m=0.2, max_range_m=30, sector_width_deg=30,
        )
        load_observation(clear)
        centre_clear = next(item for item in clear["payload"]["sectors"] if item["yaw_deg"] == 0.0)
        self.assertEqual(centre_clear["coverage"], "clear")

    def test_rgb_is_refused_not_misread_as_depth(self) -> None:
        frame = CameraFrame("front-rgb", FrameEncoding.RGB8, 1, 1, b"\x00\x00\x00", NOW)
        with self.assertRaisesRegex(DepthObstacleError, "never RGB"):
            depth_obstacle_observation(frame, vehicle_id="x500", horizontal_fov_deg=90, min_range_m=.2, max_range_m=30)


if __name__ == "__main__":
    unittest.main()
