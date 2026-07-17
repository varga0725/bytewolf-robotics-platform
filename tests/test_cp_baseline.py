"""Scoring the Collision Prevention baseline measurement, failing closed.

The evaluation is pure, so these tests need no SITL: they feed it pose tracks
and check that it reads a flight that flew up to the obstacle as unshielded, one
that stayed back as intervened, and one that never approached as inconclusive.
"""

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from simulation.perception.collision_prevention_baseline import (
    evaluate_cp_baseline,
    pose_track_from_stream,
    write_report,
)


_OBSTACLE = (0.0, 10.0)
_NOW = datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)


def _approach_track(closest_north: float) -> list[tuple[float, float]]:
    """A flight from origin northward, stopping at closest_north."""
    return [(0.0, y / 2.0) for y in range(0, int(closest_north * 2) + 1)]


class EvaluateCpBaselineTests(unittest.TestCase):
    def test_a_flight_that_reaches_the_obstacle_reads_as_unshielded(self) -> None:
        # Flew to 9.5 m north, 0.5 m short of the box centre at 10 m: ~0 clearance.
        report = evaluate_cp_baseline(_approach_track(9.5), _OBSTACLE, cp_dist_m=5.0)

        self.assertTrue(report.measured)
        self.assertFalse(report.cp_intervened)
        self.assertLess(report.min_clearance_m, 1.0)
        self.assertIn("did not intervene", report.detail)

    def test_a_flight_held_at_cp_dist_reads_as_intervened(self) -> None:
        # Stopped at 4 m north: clearance to the box surface is 10 - 4 - 0.5 = 5.5 m.
        report = evaluate_cp_baseline(_approach_track(4.0), _OBSTACLE, cp_dist_m=5.0)

        self.assertTrue(report.measured)
        self.assertTrue(report.cp_intervened)
        self.assertIn("held its distance", report.detail)

    def test_a_flight_that_never_approached_is_inconclusive(self) -> None:
        report = evaluate_cp_baseline([(0.0, 0.0), (1.0, 0.0)], _OBSTACLE, cp_dist_m=5.0)

        self.assertFalse(report.measured)
        self.assertEqual(report.verdict, "inconclusive")
        self.assertIn("never came within", report.detail)

    def test_no_pose_is_inconclusive_not_a_pass(self) -> None:
        report = evaluate_cp_baseline([], _OBSTACLE, cp_dist_m=5.0)

        self.assertEqual(report.verdict, "inconclusive")
        self.assertIn("No vehicle pose", report.detail)

    def test_a_non_positive_cp_dist_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "CP_DIST must be positive"):
            evaluate_cp_baseline(_approach_track(9.5), _OBSTACLE, cp_dist_m=0.0)


class PoseTrackParsingTests(unittest.TestCase):
    def test_extracts_only_the_named_model(self) -> None:
        lines = [
            json.dumps({"pose": [
                {"name": "ground_plane", "position": {}},
                {"name": "x500_lidar_2d_0", "position": {"x": 0.0, "y": 3.0, "z": 2.0}},
                {"name": "obstacle_north", "position": {"x": 0.0, "y": 10.0}},
            ]}),
            "",
            '{"pose": [{"name": "x500_lidar_2d_0", "positi',  # truncated tail
        ]

        track = pose_track_from_stream(lines, "x500_lidar_2d_0")

        self.assertEqual(track, [(0.0, 3.0)])


class WriteReportTests(unittest.TestCase):
    def test_records_the_mission_mode_and_the_cp_caveat(self) -> None:
        report = evaluate_cp_baseline(_approach_track(9.5), _OBSTACLE, cp_dist_m=5.0)
        with TemporaryDirectory() as directory:
            path = write_report(report, Path(directory), now=lambda: _NOW)
            document = json.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(document["verdict"], "measured")
        self.assertFalse(document["cp_intervened"])
        self.assertIn("Position mode", document["note"])
        self.assertIn("goto_location", document["flight_mode"])
        self.assertIn("cp-baseline-", path.name)


if __name__ == "__main__":
    unittest.main()
