"""Scoring the down-camera estimator against ground truth, failing closed.

The comparison is pure, so it needs no SITL: an estimate near the true offset
matches, one on the wrong side or the wrong distance does not, and an absent
capture blocks rather than passing.
"""

import unittest

from simulation.perception.target_ground_truth import (
    evaluate_ground_truth,
    _blocked_report,
)


class EvaluateGroundTruthTests(unittest.TestCase):
    def test_an_estimate_near_the_true_offset_matches(self) -> None:
        report = evaluate_ground_truth(2.0, 1.0, 2.1, 0.9, altitude_m=8.0)

        self.assertTrue(report.matched)
        self.assertLess(report.error_m, 0.5)

    def test_a_flipped_sign_is_a_mismatch(self) -> None:
        """The failure a ground-truth check exists to catch: right sign, wrong side."""
        report = evaluate_ground_truth(-2.0, -1.0, 2.0, 1.0, altitude_m=8.0)

        self.assertFalse(report.matched)
        self.assertEqual(report.verdict, "mismatched")
        self.assertIn("disagrees with ground truth", report.detail)

    def test_a_wrong_distance_is_a_mismatch(self) -> None:
        report = evaluate_ground_truth(6.0, 0.0, 2.0, 0.0, altitude_m=8.0)

        self.assertFalse(report.matched)

    def test_the_tolerance_admits_hover_drift_and_centroid_error(self) -> None:
        report = evaluate_ground_truth(2.0, 1.0, 3.0, 1.5, altitude_m=8.0, tolerance_m=1.5)

        self.assertTrue(report.matched)

    def test_a_blocked_run_is_not_a_match(self) -> None:
        report = _blocked_report("No frame captured.")

        self.assertFalse(report.matched)
        self.assertEqual(report.verdict, "blocked")
        self.assertIsNone(report.error_m)


if __name__ == "__main__":
    unittest.main()
