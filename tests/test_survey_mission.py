"""A survey is one reviewable step and many gate-checked waypoints.

The operator states an area; the gate still sees every waypoint. Bounds that
cannot be met are refused rather than widened, because a quietly coarsened
sweep has holes in it and still reports success.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import hypot
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.point_mission import PointMissionError, build_survey_mission_spec, review_survey_mission
from apps.api.server import create_app
from apps.dashboard.telemetry import Position, TelemetrySnapshot
from brain.memory.recorder import WorldMemoryRecorder
from brain.memory.world_map import MapGrid
from brain.memory.world_memory import load_world_memory
from brain.mission_spec.survey import (
    MAX_SURVEY_WAYPOINTS,
    SurveyPatternError,
    survey_reach_m,
    survey_waypoints,
)
from brain.mission_spec.validation import load_mission_safety_profile, validate_and_compile_mission_spec
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH
from simulation.perception.survey_recorder import (
    SurveyProgress,
    pose_from_snapshot,
    record_survey_scan,
)


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"
NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
GRID = MapGrid(47.397971, 8.546164, cell_size_m=2.0)


def _profile():
    return load_mission_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)


class SurveyPatternTests(unittest.TestCase):
    def test_the_sweep_stays_inside_the_requested_circle(self) -> None:
        waypoints = survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=30, spacing_m=10)

        for north, east in waypoints:
            self.assertLessEqual(hypot(north, east), 30.0 + 1e-9)

    def test_consecutive_lines_alternate_direction(self) -> None:
        waypoints = survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=30, spacing_m=10)

        first_leg = waypoints[1][0] - waypoints[0][0]
        second_leg = waypoints[3][0] - waypoints[2][0]

        self.assertGreater(first_leg * second_leg, 0 if False else -1e9)
        self.assertLess(first_leg * second_leg, 0, "a lawnmower turns around at the end of each line")

    def test_the_pattern_moves_with_its_centre(self) -> None:
        centred = survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=10, spacing_m=5)
        shifted = survey_waypoints(centre_north_m=15, centre_east_m=-5, radius_m=10, spacing_m=5)

        self.assertEqual(len(centred), len(shifted))
        self.assertAlmostEqual(shifted[0][0] - centred[0][0], 15.0)
        self.assertAlmostEqual(shifted[0][1] - centred[0][1], -5.0)

    def test_a_spacing_too_fine_is_refused_not_widened(self) -> None:
        with self.assertRaisesRegex(SurveyPatternError, "above the"):
            survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=50, spacing_m=1)

    def test_spacing_outside_the_useful_band_is_refused(self) -> None:
        for spacing in (0.5, 20.0):
            with self.subTest(spacing=spacing), self.assertRaisesRegex(SurveyPatternError, "spacing"):
                survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=20, spacing_m=spacing)

    def test_a_tiny_area_is_not_a_survey(self) -> None:
        with self.assertRaisesRegex(SurveyPatternError, "radius"):
            survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=1, spacing_m=2)

    def test_reach_measures_the_far_edge_not_the_centre(self) -> None:
        self.assertAlmostEqual(
            survey_reach_m(centre_north_m=40, centre_east_m=0, radius_m=30), 70.0
        )

    def test_the_cap_is_a_real_bound(self) -> None:
        waypoints = survey_waypoints(centre_north_m=0, centre_east_m=0, radius_m=30, spacing_m=2)

        self.assertLessEqual(len(waypoints), MAX_SURVEY_WAYPOINTS)


class SurveyCompilationTests(unittest.TestCase):
    def _spec(self, **overrides):
        profile = _profile()
        spec = build_survey_mission_spec(
            centre_north_m=0.0,
            centre_east_m=0.0,
            radius_m=30.0,
            spacing_m=10.0,
            altitude_m=6.0,
            profile=profile,
            mission_id=str(uuid4()),
        )
        spec["steps"][1].update(overrides)
        return spec

    def test_one_step_becomes_many_gate_checked_waypoints(self) -> None:
        report = validate_and_compile_mission_spec(self._spec(), _profile())

        self.assertTrue(report.approved, [issue.message for issue in report.issues])
        assert report.mission is not None
        self.assertEqual(len(report.mission.commands), 12, "takeoff + 10 waypoints + RTL")

    def test_an_area_whose_edge_leaves_the_radius_is_refused(self) -> None:
        report = validate_and_compile_mission_spec(self._spec(centre_north_m=40.0), _profile())

        self.assertFalse(report.approved)
        self.assertIn("beyond the mission radius", "; ".join(i.message for i in report.issues))

    def test_a_survey_above_the_ceiling_is_refused(self) -> None:
        report = validate_and_compile_mission_spec(self._spec(altitude_m=40.0), _profile())

        self.assertFalse(report.approved)

    def test_the_frozen_v0_1_contract_does_not_learn_the_new_step(self) -> None:
        spec = self._spec()
        spec["schema_version"] = "0.1"

        report = validate_and_compile_mission_spec(spec, _profile())

        self.assertFalse(report.approved, "v0.1 means what it meant when it was frozen")

    def test_an_unknown_schema_version_is_refused_rather_than_guessed(self) -> None:
        spec = self._spec()
        spec["schema_version"] = "9.9"

        report = validate_and_compile_mission_spec(spec, _profile())

        self.assertFalse(report.approved)
        self.assertIn("Unknown MissionSpec schema version", "; ".join(i.message for i in report.issues))


class SurveyReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.plans = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _review(self, **overrides):
        arguments = {
            "centre_north_m": 0.0,
            "centre_east_m": 0.0,
            "radius_m": 30.0,
            "spacing_m": 10.0,
            "altitude_m": 6.0,
            "goal": "Térképezd fel a kert körüli 30 métert.",
            "profile": _profile(),
            "plan_directory": self.plans,
        }
        arguments.update(overrides)
        return review_survey_mission(**arguments)

    def test_an_approved_survey_reports_how_many_waypoints_it_will_fly(self) -> None:
        mission = self._review()

        self.assertIn("10 waypoint", mission.summary)
        self.assertEqual(mission.steps, ("TAKEOFF", "SURVEY_AREA", "RTL"))
        self.assertTrue(mission.plan_path.is_file())

    def test_an_impossible_sweep_writes_nothing(self) -> None:
        with self.assertRaises(PointMissionError):
            self._review(spacing_m=1.0, radius_m=50.0)

        self.assertEqual(list(self.plans.iterdir()), [])


class SurveyApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.executed: list[str] = []
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "chat-plan",
            execute=lambda plan: self.executed.append(plan) or "submitted",
        )
        self.client = TestClient(
            create_app(Path(self.directory.name) / "telemetry.json", gateway=gateway)
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _request(self, **overrides):
        body = {
            "centre_north_m": 0.0,
            "centre_east_m": 0.0,
            "radius_m": 30.0,
            "spacing_m": 10.0,
            "altitude_m": 6.0,
            "goal": "Térképezd fel a 30 méteres kört.",
        }
        body.update(overrides)
        return self.client.post(
            "/api/v1/missions/survey", json=body, headers={"X-ByteWolf-Session": SESSION}
        )

    def test_a_survey_is_reviewed_and_awaits_approval(self) -> None:
        response = self._request()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["steps"], ["TAKEOFF", "SURVEY_AREA", "RTL"])
        self.assertTrue(response.json()["approval_required"])
        self.assertEqual(self.executed, [], "reviewing is not executing")

    def test_a_survey_reaching_past_the_radius_is_refused(self) -> None:
        response = self._request(centre_north_m=40.0)

        self.assertEqual(response.status_code, 422)
        self.assertIn("radius", response.json()["detail"])

    def test_the_dashboard_offers_the_survey_mode(self) -> None:
        page = self.client.get("/").text

        self.assertIn('value="survey"', page)
        self.assertIn("/api/v1/missions/survey", page)
        self.assertIn('id="survey-spacing"', page)


class SurveyRecorderTests(unittest.TestCase):
    """Mapping while flying: a scan is placed only when a fresh pose exists."""

    def _snapshot(self, **overrides) -> TelemetrySnapshot:
        values = {
            "position": Position(47.397971 + 0.00018, 8.546164, 500.0, 6.0),
            "battery_percent": 80.0,
            "in_air": True,
            "captured_at": NOW.isoformat(),
            "heading_deg": 90.0,
        }
        values.update(overrides)
        return TelemetrySnapshot(**values)

    def test_a_fresh_snapshot_places_the_vehicle_on_the_grid(self) -> None:
        pose = pose_from_snapshot(self._snapshot(), GRID, NOW)

        assert pose is not None
        self.assertAlmostEqual(pose.north_m, 20.0, delta=0.5)
        self.assertAlmostEqual(pose.east_m, 0.0, delta=0.5)
        self.assertEqual(pose.yaw_deg, 90.0)

    def test_a_missing_heading_is_not_north(self) -> None:
        self.assertIsNone(pose_from_snapshot(self._snapshot(heading_deg=None), GRID, NOW))

    def test_a_stale_snapshot_is_not_the_current_position(self) -> None:
        self.assertIsNone(pose_from_snapshot(self._snapshot(), GRID, NOW + timedelta(seconds=10)))

    def test_a_missing_position_places_nothing(self) -> None:
        self.assertIsNone(pose_from_snapshot(self._snapshot(position=None), GRID, NOW))

    def test_a_scan_without_a_usable_pose_is_counted_not_placed(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "claims.jsonl"
            progress = record_survey_scan(
                _scan_message(), self._snapshot(heading_deg=None), WorldMemoryRecorder(path), GRID, NOW
            )

            self.assertEqual(progress.dropped_no_pose, 1)
            self.assertEqual(progress.claims_written, 0)
            self.assertFalse(path.exists())

    def test_a_paired_scan_lands_on_the_map_where_the_vehicle_was(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "claims.jsonl"

            progress = record_survey_scan(
                _scan_message(), self._snapshot(), WorldMemoryRecorder(path), GRID, NOW, progress=SurveyProgress()
            )

            claims = load_world_memory(path).recall(NOW)
            self.assertEqual(progress.scans_mapped, 1)
            self.assertGreater(progress.claims_written, 0)
            self.assertIn("map_region", {claim.category for claim in claims})


def _scan_message() -> dict:
    """A 270° gz scan seeing a wall straight ahead.

    The wall spans a whole sector: a handful of stray beams would resolve to a
    low sector confidence and be filtered out of recall, which is the sensor
    contract working, not a recorder bug.
    """
    from math import radians

    ranges: list[float | str] = ["Infinity"] * 1080
    for index in range(510, 571):
        ranges[index] = 8.0
    return {
        "angleMin": radians(-135.0),
        "angleStep": radians(270.0) / 1079,
        "ranges": ranges,
        "rangeMin": 0.1,
        "rangeMax": 30.0,
    }


if __name__ == "__main__":
    unittest.main()
