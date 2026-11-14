"""Acceptance CLI approval boundary and ULog enforcement."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from brain.cli import acceptance_demo_waypoint_mission
from brain.mission.demo_waypoint_mission import (
    _document_has_visual_evidence,
    acceptance_run_is_clean,
    consecutive_acceptance_summary,
)
from brain.mission.demo_waypoint_reviewed_plan import write_reviewed_demo_waypoint_plan
from brain.mission.demo_waypoint_mission import DemoWaypointMissionConfig, default_demo_waypoint_loop
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class AcceptanceApprovalTests(unittest.TestCase):
    def _plan(self, directory: Path) -> Path:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        config = DemoWaypointMissionConfig(
            takeoff_altitude_m=8.0,
            waypoints=default_demo_waypoint_loop(),
            max_mission_items=12,
        )
        plan = directory / "demo.demo-waypoint-mission.json"
        write_reviewed_demo_waypoint_plan(plan, config, mission_id="mid")
        approval = plan.with_name(f"{plan.name}.approval.json")
        approval.unlink(missing_ok=True)
        return plan

    def test_missing_approval_without_operator_flag_refuses(self) -> None:
        with TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            with patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False):
                os.environ["BYTEWOLF_SITL_ACCEPTANCE"] = "1"
                with self.assertRaises(RuntimeError):
                    acceptance_demo_waypoint_mission._ensure_plan_approval(
                        plan, record_approval=False, confirm=False
                    )

    def test_mismatched_hash_refuses(self) -> None:
        with TemporaryDirectory() as directory:
            plan = self._plan(Path(directory))
            plan.write_text(plan.read_text() + "\n", encoding="utf-8")
            approval = plan.with_name(f"{plan.name}.approval.json")
            approval.write_text(
                json.dumps(
                    {
                        "schema_version": "demo-waypoint-mission-approval.v0.1",
                        "approved_at": "2020-01-01T00:00:00+00:00",
                        "mission_type": "demo-waypoint-mission",
                        "mission_id": "mid",
                        "plan_filename": plan.name,
                        "plan_sha256": "deadbeef",
                        "safety_decision": "approved",
                    }
                ),
                encoding="utf-8",
            )
            from brain.mission.demo_waypoint_reviewed_plan import require_matching_demo_waypoint_approval

            with self.assertRaises(RuntimeError):
                require_matching_demo_waypoint_approval(plan)


class AcceptanceEvidenceTests(unittest.TestCase):
    def test_missing_ulog_marks_run_unclean(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": None,
        }
        self.assertFalse(acceptance_run_is_clean(document))

    def test_fallback_disarm_reason_marks_run_unclean(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "ulog_status": "captured",
            "disarm_reason": "disarm on ground after landing wait timeout",
            "screen_capture": {"status": "captured", "relative_path": "screen.mp4"},
        }
        self.assertFalse(acceptance_run_is_clean(document))

    def test_missing_visual_evidence_marks_run_unclean_by_default(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {"status": "captured_no_gui"},
        }
        self.assertFalse(acceptance_run_is_clean(document))

    def test_screenshot_counts_as_visual_evidence_when_valid(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture_screenshot_relative_path": "screen.png",
            "screen_capture_screenshot_gui_alive": True,
            "screen_capture_screenshot_non_uniform": True,
        }
        self.assertTrue(acceptance_run_is_clean(document))

    def test_failed_capture_with_dead_gui_screenshot_is_not_visual_evidence(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {
                "status": "failed",
                "gz_gui_alive_at_stop": False,
                "gz_gui_exit_code": 1,
                "screenshot_relative_path": "s.png",
                "screenshot_gui_alive": False,
            },
        }
        self.assertFalse(_document_has_visual_evidence(document))
        self.assertFalse(acceptance_run_is_clean(document))

    def test_captured_video_requires_human_visual_review_when_flagged(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {"status": "captured", "relative_path": "screen.mp4"},
            "visual_review_required": True,
            "visual_human_review": "pending",
        }
        self.assertFalse(acceptance_run_is_clean(document))

    def test_captured_video_counts_after_human_visual_review(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture": {"status": "captured", "relative_path": "screen.mp4"},
            "visual_review_required": True,
            "visual_human_review": "approved",
        }
        self.assertTrue(acceptance_run_is_clean(document))

    def test_bare_screenshot_path_without_gui_alive_flags_is_not_visual_evidence(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
            "screen_capture_screenshot_relative_path": "screen.png",
        }
        self.assertFalse(_document_has_visual_evidence(document))
        self.assertFalse(acceptance_run_is_clean(document))

    def test_visual_evidence_requirement_can_be_disabled(self) -> None:
        document = {
            "overall_status": "passed",
            "trajectory_png_relative_path": "trajectory.png",
            "px4_ulog_relative_path": "runs/x.ulg",
        }
        self.assertFalse(acceptance_run_is_clean(document))
        self.assertTrue(acceptance_run_is_clean(document, require_visual_evidence=False))

    def test_summary_requires_ulog_for_pass(self) -> None:
        runs = [
            {
                "overall_status": "passed",
                "trajectory_png_relative_path": "t.png",
                "px4_ulog_relative_path": "runs/x.ulg",
            },
            {
                "overall_status": "passed",
                "trajectory_png_relative_path": "t.png",
                "px4_ulog_relative_path": None,
            },
        ]
        summary = consecutive_acceptance_summary(runs, required_runs=2)
        self.assertEqual(summary["overall_status"], "failed")


if __name__ == "__main__":
    unittest.main()
