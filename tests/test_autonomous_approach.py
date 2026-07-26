"""Scoring the autonomous approach, failing closed.

The verdict is a pure function of what perception decided and where the vehicle
ended up: an approved move that lands over the marker is "reached", one that
lands elsewhere is "missed", a run that proposed no move stayed safely put, and
an unreadable final pose blocks rather than passing. Only the runner touches
SITL; this pins the scoring the same way the ground-truth check is pinned.
"""

import unittest

from simulation.perception.autonomous_approach import evaluate_approach


class EvaluateApproachTests(unittest.TestCase):
    def test_a_move_that_lands_over_the_marker_is_reached(self) -> None:
        report = evaluate_approach(
            target_detected=True, move_approved=True, refusal_reason=None,
            final_north_m=3.1, final_east_m=1.9, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertTrue(report.reached)
        self.assertEqual(report.verdict, "reached")
        self.assertLess(report.final_offset_to_marker_m, 0.5)

    def test_a_move_that_lands_far_from_the_marker_is_missed(self) -> None:
        report = evaluate_approach(
            target_detected=True, move_approved=True, refusal_reason=None,
            final_north_m=0.0, final_east_m=0.0, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertFalse(report.reached)
        self.assertEqual(report.verdict, "missed")
        self.assertIn("did not deliver", report.detail)

    def test_a_flipped_final_position_is_a_miss_not_a_match(self) -> None:
        # The failure a ground-truth arrival check exists to catch: right distance,
        # wrong side of the marker.
        report = evaluate_approach(
            target_detected=True, move_approved=True, refusal_reason=None,
            final_north_m=-3.0, final_east_m=-2.0, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertFalse(report.reached)

    def test_no_proposed_move_is_a_safe_outcome_not_a_match(self) -> None:
        report = evaluate_approach(
            target_detected=False, move_approved=False, refusal_reason="No marker in view.",
            final_north_m=0.0, final_east_m=0.0, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertFalse(report.reached)
        self.assertEqual(report.verdict, "no_move")
        self.assertIn("No marker in view.", report.detail)

    def test_a_refused_target_reports_why_it_was_refused(self) -> None:
        report = evaluate_approach(
            target_detected=True, move_approved=False, refusal_reason="The target fix is too uncertain.",
            final_north_m=1.0, final_east_m=1.0, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertEqual(report.verdict, "no_move")
        self.assertIn("too uncertain", report.detail)

    def test_an_unreadable_final_pose_blocks_rather_than_matches(self) -> None:
        report = evaluate_approach(
            target_detected=True, move_approved=True, refusal_reason=None,
            final_north_m=None, final_east_m=None, marker_north_m=3.0, marker_east_m=2.0,
        )

        self.assertFalse(report.reached)
        self.assertEqual(report.verdict, "blocked")


if __name__ == "__main__":
    unittest.main()
