"""The NIM mission CLI must require explicit, immutable reviewed plans."""

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.cli.fly_nim_mission import (
    _load_approved_plan,
    _write_reviewed_plan,
    parse_arguments,
)
from brain.mission_spec.validation import load_mission_safety_profile


PROFILE = load_mission_safety_profile("shared/config/x500v2/twin.yaml")


def _reviewable_spec() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "mission_id": "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3",
        "vehicle_id": PROFILE.vehicle_id,
        "intent": "test_flight",
        "constraints": {
            "max_altitude_m": PROFILE.max_altitude_m,
            "max_speed_m_s": PROFILE.max_speed_m_s,
            "max_radius_m": PROFILE.max_radius_m,
            "minimum_battery_percent_to_start": PROFILE.minimum_battery_percent_to_start,
            "loss_of_link_action": PROFILE.loss_of_link_action,
        },
        "steps": [
            {"type": "TAKEOFF", "altitude_m": 2.0},
            {"type": "HOLD", "duration_s": 3.0},
            {"type": "LAND"},
        ],
        "abort_policy": {
            "on_timeout": "LAND",
            "on_low_battery": PROFILE.loss_of_link_action,
            "on_position_invalid": "LAND",
        },
    }


class NIMMissionCliTests(unittest.TestCase):
    def test_is_a_dry_run_without_explicit_execute_flag(self) -> None:
        arguments = parse_arguments(["--command", "take off two metres then land"])

        self.assertFalse(arguments.execute)

    def test_execution_requires_the_explicit_flag(self) -> None:
        arguments = parse_arguments(["--command", "take off two metres then land", "--execute"])

        self.assertTrue(arguments.execute)

    def test_a_reviewed_plan_can_be_selected_for_execution(self) -> None:
        arguments = parse_arguments(["--mission-spec-file", "reviewed.json", "--execute"])

        self.assertTrue(arguments.execute)
        self.assertEqual(str(arguments.mission_spec_file), "reviewed.json")

    def test_execution_plan_requires_a_matching_review_approval(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "mission.json"
            _write_reviewed_plan(plan_path, _reviewable_spec(), "test-model")

            document, mission = _load_approved_plan(plan_path, PROFILE)

        self.assertEqual(document["mission_id"], mission.mission_id)

    def test_execution_rejects_a_plan_changed_after_review(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "mission.json"
            _write_reviewed_plan(plan_path, _reviewable_spec(), "test-model")
            modified = json.loads(plan_path.read_text(encoding="utf-8"))
            modified["steps"][0]["altitude_m"] = 3.0
            plan_path.write_text(json.dumps(modified), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "differs from the safety-approved plan"):
                _load_approved_plan(plan_path, PROFILE)

    def test_execution_rejects_a_plan_without_a_review_approval(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            plan_path = Path(temporary_directory) / "mission.json"
            plan_path.write_text(json.dumps(_reviewable_spec()), encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "has no approval record"):
                _load_approved_plan(plan_path, PROFILE)


if __name__ == "__main__":
    unittest.main()
