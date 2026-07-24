"""The lidar adapter must speak the obstacle contract, and only the truth.

Every test here round-trips the adapter's output through the contract's own
loader, so the adapter can never be right by its own say-so: the same schema
that guards every observation guards this one.
"""

from datetime import UTC, datetime
import json
from math import inf, nan, radians
from pathlib import Path
import unittest

from brain.perception.lidar_obstacle import (
    LaserScan,
    LidarObstacleError,
    laser_scan_from_gz_json,
    obstacle_observation,
)
from brain.telemetry.observation import ObservationState, load_observation


_GROUND_TRUTH_SCAN = Path(__file__).resolve().parent / "fixtures/lidar/scan_front_and_left_obstacles.json"


_OBSERVED_AT = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)
_FRESH = datetime(2026, 7, 17, 12, 0, 0, 100000, tzinfo=UTC)


def _scan_270deg(ranges: list[float], *, samples: int = 1080) -> LaserScan:
    """A lidar_2d_v2-shaped scan: 270 degrees, forward centred, gz CCW angles."""
    angle_min = radians(-135.0)
    increment = radians(270.0) / (samples - 1)
    if len(ranges) != samples:
        ranges = ranges + [inf] * (samples - len(ranges))
    return LaserScan(angle_min, increment, tuple(ranges), 0.1, 30.0)


def _beam_at(bearing_deg: float, samples: int = 1080) -> int:
    angle_min = radians(-135.0)
    increment = radians(270.0) / (samples - 1)
    return round((radians(bearing_deg) - angle_min) / increment)


def _with_obstacle(bearing_deg: float, distance_m: float, *, spread: int = 3) -> LaserScan:
    ranges = [inf] * 1080
    centre = _beam_at(bearing_deg)
    for index in range(centre - spread, centre + spread + 1):
        ranges[index] = distance_m
    return _scan_270deg(ranges)


def _sectors(document: dict) -> list[dict]:
    return document["payload"]["sectors"]


def _sector_at(document: dict, yaw_deg: float) -> dict:
    return next(sector for sector in _sectors(document) if sector["yaw_deg"] == yaw_deg)


class ContractComplianceTests(unittest.TestCase):
    def test_output_is_a_valid_observation_the_contract_accepts(self) -> None:
        document = obstacle_observation(
            _scan_270deg([inf] * 1080), vehicle_id="x500v2_reference_01",
            observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        observation = load_observation(document)

        self.assertEqual(observation.kind, "obstacle")
        self.assertEqual(observation.state(_FRESH), ObservationState.VALID)

    def test_tiles_the_full_circle_in_sectors(self) -> None:
        document = obstacle_observation(
            _scan_270deg([inf] * 1080), vehicle_id="v", observed_at=_OBSERVED_AT,
            sensor_id="lidar_2d", sector_width_deg=15.0,
        )

        self.assertEqual(len(_sectors(document)), 24)


class FrameConventionTests(unittest.TestCase):
    """gz measures counter-clockwise; the obstacle frame's yaw is clockwise."""

    def test_a_left_obstacle_lands_on_a_negative_yaw_sector(self) -> None:
        document = obstacle_observation(
            _with_obstacle(90.0, 4.2), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        measured = [sector for sector in _sectors(document) if sector["coverage"] == "measured"]
        self.assertEqual([sector["yaw_deg"] for sector in measured], [-90.0])

    def test_a_right_obstacle_lands_on_a_positive_yaw_sector(self) -> None:
        document = obstacle_observation(
            _with_obstacle(-90.0, 4.2), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        measured = [sector for sector in _sectors(document) if sector["coverage"] == "measured"]
        self.assertEqual([sector["yaw_deg"] for sector in measured], [90.0])

    def test_a_forward_obstacle_lands_straight_ahead(self) -> None:
        document = obstacle_observation(
            _with_obstacle(0.0, 4.2), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        self.assertEqual(_sector_at(document, 0.0)["coverage"], "measured")


class CoverageTests(unittest.TestCase):
    def test_the_rear_blind_spot_is_unobserved_never_clear(self) -> None:
        """The 270 degree lidar cannot see the 90 degrees behind the vehicle."""
        document = obstacle_observation(
            _scan_270deg([inf] * 1080), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        rear = [sector for sector in _sectors(document) if abs(sector["yaw_deg"]) >= 150.0]
        self.assertTrue(rear)
        self.assertTrue(all(sector["coverage"] == "unobserved" for sector in rear))
        self.assertFalse(any(sector["coverage"] == "clear" for sector in rear))

    def test_a_swept_bearing_with_no_return_is_clear(self) -> None:
        document = obstacle_observation(
            _scan_270deg([inf] * 1080), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        self.assertEqual(_sector_at(document, 0.0)["coverage"], "clear")
        self.assertNotIn("distance_m", _sector_at(document, 0.0))

    def test_a_return_at_the_sensor_edge_is_clear_not_an_edge_obstacle(self) -> None:
        document = obstacle_observation(
            _with_obstacle(0.0, 30.0), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        self.assertEqual(_sector_at(document, 0.0)["coverage"], "clear")

    def test_a_full_circle_scan_leaves_no_bearing_unobserved(self) -> None:
        angle_min = radians(-180.0)
        samples = 720
        increment = radians(360.0) / samples
        scan = LaserScan(angle_min, increment, tuple([inf] * samples), 0.1, 30.0)

        document = obstacle_observation(scan, vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_360")

        self.assertFalse(any(sector["coverage"] == "unobserved" for sector in _sectors(document)))


class DistanceTests(unittest.TestCase):
    def test_a_measured_sector_reports_the_nearest_return_not_the_average(self) -> None:
        ranges = [inf] * 1080
        centre = _beam_at(0.0)
        ranges[centre] = 8.0
        ranges[centre + 1] = 3.0  # the nearer obstacle in the same sector
        ranges[centre - 1] = 9.0
        document = obstacle_observation(
            _scan_270deg(ranges), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        self.assertEqual(_sector_at(document, 0.0)["distance_m"], 3.0)

    def test_clear_and_unobserved_sectors_carry_no_distance(self) -> None:
        document = obstacle_observation(
            _scan_270deg([inf] * 1080), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        for sector in _sectors(document):
            if sector["coverage"] != "measured":
                self.assertNotIn("distance_m", sector)

    def test_confidence_reflects_how_much_of_the_sector_returned(self) -> None:
        document = obstacle_observation(
            _with_obstacle(0.0, 4.2, spread=3), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        measured = _sector_at(document, 0.0)
        self.assertLess(measured["confidence"], 1.0)
        self.assertGreater(measured["confidence"], 0.0)


class MalformedScanTests(unittest.TestCase):
    def test_an_empty_scan_is_refused(self) -> None:
        with self.assertRaisesRegex(LidarObstacleError, "at least one beam"):
            obstacle_observation(
                LaserScan(0.0, 0.01, (), 0.1, 30.0), vehicle_id="v",
                observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
            )

    def test_a_zero_increment_scan_is_refused(self) -> None:
        with self.assertRaisesRegex(LidarObstacleError, "increment is zero"):
            obstacle_observation(
                LaserScan(0.0, 0.0, (1.0, 2.0), 0.1, 30.0), vehicle_id="v",
                observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
            )

    def test_a_non_finite_scan_field_is_refused(self) -> None:
        with self.assertRaisesRegex(LidarObstacleError, "must be finite"):
            obstacle_observation(
                LaserScan(nan, 0.01, (1.0,), 0.1, 30.0), vehicle_id="v",
                observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
            )

    def test_a_bad_range_order_is_refused(self) -> None:
        inverted_range = LaserScan(radians(-135.0), 0.01, tuple([inf] * 10), 30.0, 0.1)

        with self.assertRaisesRegex(LidarObstacleError, "range_min_m < range_max_m"):
            obstacle_observation(
                inverted_range, vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
            )

    def test_a_naive_timestamp_is_refused(self) -> None:
        with self.assertRaisesRegex(LidarObstacleError, "timezone-aware"):
            obstacle_observation(
                _scan_270deg([inf] * 1080), vehicle_id="v",
                observed_at=datetime(2026, 7, 17, 12, 0, 0), sensor_id="lidar_2d",
            )

    def test_a_nan_beam_does_not_count_as_an_obstacle(self) -> None:
        ranges = [inf] * 1080
        ranges[_beam_at(0.0)] = nan
        document = obstacle_observation(
            _scan_270deg(ranges), vehicle_id="v", observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        # A NaN return is not a measurement, so the swept sector stays clear.
        self.assertEqual(_sector_at(document, 0.0)["coverage"], "clear")


class GzJsonParsingTests(unittest.TestCase):
    def test_parses_camelcase_fields_and_infinity_sentinels(self) -> None:
        message = {
            "angleMin": -2.356195, "angleMax": 2.356195, "angleStep": 0.00436737,
            "rangeMin": 0.1, "rangeMax": 30.0, "count": 3,
            "ranges": ["Infinity", 4.2, "Infinity"],
        }

        scan = laser_scan_from_gz_json(message)

        self.assertEqual(scan.angle_min_rad, -2.356195)
        self.assertEqual(scan.ranges_m, (inf, 4.2, inf))
        self.assertEqual((scan.range_min_m, scan.range_max_m), (0.1, 30.0))

    def test_refuses_a_message_missing_a_field(self) -> None:
        with self.assertRaisesRegex(LidarObstacleError, "missing or malforming"):
            laser_scan_from_gz_json({"angleMin": 0.0, "ranges": [1.0]})

    def test_refuses_an_unparseable_range(self) -> None:
        message = {
            "angleMin": 0.0, "angleStep": 0.01, "rangeMin": 0.1, "rangeMax": 30.0,
            "ranges": ["not-a-number"],
        }
        with self.assertRaisesRegex(LidarObstacleError, "is not a number"):
            laser_scan_from_gz_json(message)


class GroundTruthScanTests(unittest.TestCase):
    """A real scan captured from SITL with obstacles at known bearings.

    Front box at world +X (drone facing +X) and left box at world +Y. This is
    the empirical confirmation the synthetic frame tests cannot give: the sign
    of the gz-to-FRD flip is checked against physical ground truth, not asserted.
    """

    def test_the_captured_scan_matches_the_sensor_the_baseline_pins(self) -> None:
        message = json.loads(_GROUND_TRUTH_SCAN.read_text(encoding="utf-8"))
        scan = laser_scan_from_gz_json(message)

        self.assertEqual(len(scan.ranges_m), 1080)
        self.assertAlmostEqual(scan.range_max_m, 30.0)
        self.assertAlmostEqual(scan.range_min_m, 0.1)

    def test_a_front_obstacle_appears_straight_ahead_and_a_left_one_at_negative_yaw(self) -> None:
        message = json.loads(_GROUND_TRUTH_SCAN.read_text(encoding="utf-8"))
        document = obstacle_observation(
            laser_scan_from_gz_json(message), vehicle_id="x500v2_reference_01",
            observed_at=_OBSERVED_AT, sensor_id="lidar_2d",
        )

        load_observation(document)  # the contract accepts a real scan
        measured = {sector["yaw_deg"]: sector["distance_m"] for sector in _sectors(document) if sector["coverage"] == "measured"}
        self.assertIn(0.0, measured, "front box should be straight ahead")
        self.assertIn(-90.0, measured, "left box should be at negative yaw")
        # Sanity on the physical distances: front box near face ~4.4 m, left ~5.5 m.
        self.assertAlmostEqual(measured[0.0], 4.4, delta=0.3)
        self.assertAlmostEqual(measured[-90.0], 5.5, delta=0.3)


if __name__ == "__main__":
    unittest.main()
