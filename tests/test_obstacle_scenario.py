"""Scoring the obstacle path against a placed obstacle, failing closed.

The evaluation is a pure function, so these tests need no SITL: they feed it the
real ground-truth scan and synthetic variations, and check that it only passes
when the known obstacle was actually and repeatedly seen.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
import unittest

from simulation.perception.obstacle_scenario import (
    ExpectedObstacle,
    evaluate_obstacle_scenario,
    observations_from_scans,
    write_report,
)


_GROUND_TRUTH_SCAN = (
    Path(__file__).resolve().parent / "fixtures/lidar/scan_front_and_left_obstacles.json"
)
_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _real_observations(count: int) -> list[dict]:
    message = json.loads(_GROUND_TRUTH_SCAN.read_text(encoding="utf-8"))
    return observations_from_scans(
        [message] * count, vehicle_id="x500v2_reference_01", sensor_id="lidar_2d", now=lambda: _NOW
    )


class EvaluateObstacleScenarioTests(unittest.TestCase):
    def test_passes_when_the_known_obstacle_is_seen_on_every_scan(self) -> None:
        report = evaluate_obstacle_scenario(
            _real_observations(20), ExpectedObstacle(sector_yaw_deg=0.0, distance_m=4.4)
        )

        self.assertTrue(report.passed)
        self.assertEqual(report.detection_rate, 1.0)
        self.assertEqual(report.false_negative_rate, 0.0)
        self.assertTrue(report.blind_spot_always_unobserved)

    def test_confirms_the_left_obstacle_at_negative_yaw(self) -> None:
        """The same real scan carries the left box at yaw -90; ground truth both ways."""
        report = evaluate_obstacle_scenario(
            _real_observations(10), ExpectedObstacle(sector_yaw_deg=-90.0, distance_m=5.5)
        )

        self.assertTrue(report.passed)

    def test_fails_when_the_obstacle_is_on_the_wrong_sector(self) -> None:
        """A box that is actually ahead must not be accepted as one to the right."""
        report = evaluate_obstacle_scenario(
            _real_observations(20), ExpectedObstacle(sector_yaw_deg=45.0, distance_m=4.4)
        )

        self.assertFalse(report.passed)
        self.assertEqual(report.detections, 0)
        self.assertIn("never detected", report.detail)

    def test_fails_when_the_measured_distance_is_far_from_ground_truth(self) -> None:
        report = evaluate_obstacle_scenario(
            _real_observations(20),
            ExpectedObstacle(sector_yaw_deg=0.0, distance_m=15.0, distance_tolerance_m=0.5),
        )

        self.assertFalse(report.passed)
        self.assertIn("outside", report.detail)

    def test_fails_when_detection_is_intermittent(self) -> None:
        real = _real_observations(2)
        missing = _blank_observation()
        report = evaluate_obstacle_scenario(
            [real[0]] + [missing] * 8, ExpectedObstacle(sector_yaw_deg=0.0, distance_m=4.4)
        )

        self.assertFalse(report.passed)
        self.assertLess(report.detection_rate, 0.9)

    def test_fails_closed_on_no_scans(self) -> None:
        report = evaluate_obstacle_scenario([], ExpectedObstacle(sector_yaw_deg=0.0, distance_m=4.4))

        self.assertFalse(report.passed)
        self.assertEqual(report.scans, 0)
        self.assertIn("No scans", report.detail)


class WriteReportTests(unittest.TestCase):
    def test_writes_a_durable_artifact_naming_its_evidence_level(self) -> None:
        from tempfile import TemporaryDirectory

        report = evaluate_obstacle_scenario(
            _real_observations(5), ExpectedObstacle(sector_yaw_deg=0.0, distance_m=4.4)
        )
        with TemporaryDirectory() as directory:
            path = write_report(report, Path(directory), now=lambda: _NOW)
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["sensor"], "gz_x500_lidar_2d")
        self.assertEqual(document["verification_level"], "px4-gazebo-fault-injection")
        self.assertEqual(document["verdict"], "passed")
        self.assertIn("obstacle-", path.name)


def _blank_observation() -> dict:
    """A contract-valid observation whose forward sector saw nothing."""
    return {
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": "2026-07-17T12:00:00Z",
        "max_age_s": 0.3,
        "validity": "valid",
        "payload": {
            "frame": "body_frd",
            "sensor": {"id": "lidar_2d", "min_range_m": 0.1, "max_range_m": 30.0},
            "sectors": [{"yaw_deg": 0.0, "width_deg": 15.0, "coverage": "clear"}],
        },
    }


if __name__ == "__main__":
    unittest.main()
