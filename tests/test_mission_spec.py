"""Tests for the safety boundary around the MissionSpec v0.1 contract."""

from copy import deepcopy
import json
from pathlib import Path
import unittest

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.mission_spec.validation import (
    MissionSafetyProfile,
    validate_and_compile_mission_spec,
)


PROFILE = MissionSafetyProfile(
    vehicle_id="x500v2_reference_01",
    max_altitude_m=20.0,
    max_speed_m_s=3.0,
    max_radius_m=50.0,
    minimum_battery_percent_to_start=40.0,
    loss_of_link_action="RTL",
)
ROOT = Path(__file__).resolve().parents[1]


def valid_spec() -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "mission_id": "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3",
        "vehicle_id": "x500v2_reference_01",
        "intent": "test_flight",
        "constraints": {
            "max_altitude_m": 10.0,
            "max_speed_m_s": 3.0,
            "max_radius_m": 25.0,
            "minimum_battery_percent_to_start": 40.0,
            "loss_of_link_action": "RTL",
        },
        "steps": [
            {"type": "TAKEOFF", "altitude_m": 2.0},
            {"type": "GOTO_LOCAL", "north_m": 5.0, "east_m": 0.0, "down_m": -2.0},
            {"type": "HOLD", "duration_s": 3.0},
            {"type": "RTL"},
        ],
        "abort_policy": {
            "on_timeout": "LAND",
            "on_low_battery": "RTL",
            "on_position_invalid": "LAND",
        },
    }


class MissionSpecValidationTests(unittest.TestCase):
    def test_compiles_a_valid_bounded_takeoff_waypoint_return_mission(self) -> None:
        report = validate_and_compile_mission_spec(valid_spec(), PROFILE)

        self.assertTrue(report.approved)
        self.assertEqual(report.issues, ())
        assert report.mission is not None
        self.assertEqual(report.mission.vehicle_id, "x500v2_reference_01")
        self.assertEqual(
            report.mission.commands,
            (
                TakeoffCommand(target_altitude_m=2.0),
                WaypointCommand(north_m=5.0, east_m=0.0, target_altitude_m=2.0),
                ReturnToHomeCommand(target_altitude_m=2.0),
            ),
        )
        self.assertEqual(report.mission.hold_durations_s, (3.0,))

    def test_rejects_an_unknown_root_property(self) -> None:
        document = valid_spec()
        document["raw_mavlink_command"] = "DO_SET_MODE"

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("Additional properties are not allowed", report.issues[0].message)

    def test_rejects_a_mission_that_relaxes_the_platform_altitude_limit(self) -> None:
        document = valid_spec()
        constraints = document["constraints"]
        assert isinstance(constraints, dict)
        constraints["max_altitude_m"] = 21.0

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("platform maximum", report.issues[0].message)

    def test_rejects_a_waypoint_outside_the_mission_radius(self) -> None:
        document = valid_spec()
        steps = document["steps"]
        assert isinstance(steps, list)
        waypoint = steps[1]
        assert isinstance(waypoint, dict)
        waypoint["north_m"] = 26.0

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("mission radius", report.issues[0].message)

    def test_rejects_a_terminal_step_that_is_not_last(self) -> None:
        document = valid_spec()
        steps = document["steps"]
        assert isinstance(steps, list)
        steps.append({"type": "HOLD", "duration_s": 1.0})

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("terminal step", report.issues[0].message)

    def test_compiles_an_explicit_land_terminal_command(self) -> None:
        document = valid_spec()
        steps = document["steps"]
        assert isinstance(steps, list)
        steps[-1] = {"type": "LAND"}

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertTrue(report.approved)
        assert report.mission is not None
        self.assertEqual(report.mission.commands[-1], LandCommand())

    def test_rejects_a_vehicle_that_does_not_match_the_active_twin(self) -> None:
        document = valid_spec()
        document["vehicle_id"] = "other_vehicle"

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("active twin", report.issues[0].message)

    def test_rejects_non_finite_hold_duration(self) -> None:
        document = valid_spec()
        steps = document["steps"]
        assert isinstance(steps, list)
        hold = steps[2]
        assert isinstance(hold, dict)
        hold["duration_s"] = float("nan")

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertFalse(report.approved)
        self.assertIn("finite", report.issues[0].message)

    def test_report_is_deterministic_and_does_not_mutate_the_source_document(self) -> None:
        document = valid_spec()
        original = deepcopy(document)

        first = validate_and_compile_mission_spec(document, PROFILE)
        second = validate_and_compile_mission_spec(document, PROFILE)

        self.assertEqual(first, second)
        self.assertEqual(document, original)
        assert first.mission is not None
        self.assertEqual(len(first.mission.source_hash), 64)

    def test_documented_example_is_valid(self) -> None:
        document = json.loads(
            (ROOT / "interfaces/mission_spec/examples/takeoff_waypoint_rtl.v0_1.json").read_text()
        )

        report = validate_and_compile_mission_spec(document, PROFILE)

        self.assertTrue(report.approved)


if __name__ == "__main__":
    unittest.main()
