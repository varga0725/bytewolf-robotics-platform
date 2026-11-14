"""Acceptance CLI visual-review approval and classification."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from brain.cli import acceptance_demo_waypoint_mission


class AcceptanceVisualReviewCliTests(unittest.TestCase):
    def _clean_pending_run(self, directory: Path) -> Path:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {"status": "captured", "relative_path": "screen.mp4"},
            "visual_review_required": True,
            "visual_human_review": "pending",
            "visual_review_frames": [{"phase": "takeoff", "relative_path": "frames/a.png"}],
        }
        path = directory / "demo-waypoint-mission-test.json"
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path

    def test_approve_visual_review_refuses_technically_dirty_run(self) -> None:
        with TemporaryDirectory() as directory:
            dirty = Path(directory) / "dirty.json"
            dirty.write_text(
                json.dumps(
                    {
                        "overall_status": "passed",
                        "trajectory_png_relative_path": "t.png",
                        "px4_ulog_relative_path": None,
                        "visual_review_required": True,
                        "visual_review_frames": [{"phase": "takeoff"}],
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(RuntimeError):
                acceptance_demo_waypoint_mission.approve_visual_reviews([dirty])

    def test_approve_visual_review_updates_run_and_summary(self) -> None:
        with TemporaryDirectory() as directory:
            run_path = self._clean_pending_run(Path(directory))
            summary = acceptance_demo_waypoint_mission.approve_visual_reviews(
                [run_path],
                reviewer="operator",
                note="drone visible",
            )
            approved = json.loads(run_path.read_text(encoding="utf-8"))
            self.assertEqual(approved.get("visual_human_review"), "approved")
            self.assertEqual(approved.get("visual_reviewer"), "operator")
            self.assertEqual(approved.get("acceptance_status"), "passed")
            self.assertEqual(summary.get("overall_status"), "passed")

    def test_finalize_acceptance_keeps_mission_passed_for_pending_review(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {"status": "captured", "relative_path": "screen.mp4"},
            "visual_review_required": True,
            "visual_human_review": "pending",
            "visual_review_frames": [{"phase": "takeoff", "relative_path": "frames/a.png"}],
        }
        result = acceptance_demo_waypoint_mission._finalize_acceptance_document(
            document,
            require_visual_evidence=True,
        )
        self.assertEqual(result.get("overall_status"), "passed")
        self.assertEqual(result.get("acceptance_status"), "pending_review")


if __name__ == "__main__":
    unittest.main()