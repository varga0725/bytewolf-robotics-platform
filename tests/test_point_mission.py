"""A point picked on the map is a mission request like any other.

It skips the language model — the user already said exactly where — but it may
not skip anything else: same MissionSpec, same compiler, same SafetyGate, same
approval proof on disk, and the same single pending slot that only an explicit
approval can empty.
"""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.point_mission import PointMissionError, build_point_mission_spec, review_point_mission
from apps.api.server import create_app
from brain.mission_spec.reviewed_plan import require_matching_review_approval
from brain.mission_spec.validation import load_mission_safety_profile
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


def _profile():
    return load_mission_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)


def _beyond_the_radius_m() -> float:
    """A distance the active contract must refuse, whatever it currently is.

    A fixed 400 m was out of range only while the radius was 50 m; it became a
    legal waypoint the moment the envelope widened, and the test that named
    itself "outside the radius" would have started asserting the opposite.
    """
    return _profile().max_radius_m + 100.0


class PointMissionSpecTests(unittest.TestCase):
    def test_the_plan_flies_out_holds_and_returns(self) -> None:
        spec = build_point_mission_spec(north_m=20, east_m=-10, altitude_m=5, profile=_profile())

        self.assertEqual(
            [step["type"] for step in spec["steps"]], ["TAKEOFF", "GOTO_LOCAL", "HOLD", "RTL"]
        )
        self.assertEqual(spec["steps"][1]["down_m"], -5.0, "the target keeps the climb altitude")

    def test_the_constraints_are_copied_from_the_profile_not_invented(self) -> None:
        profile = _profile()

        spec = build_point_mission_spec(north_m=1, east_m=1, altitude_m=2, profile=profile)

        self.assertEqual(spec["constraints"]["max_altitude_m"], profile.max_altitude_m)
        self.assertEqual(spec["constraints"]["max_radius_m"], profile.max_radius_m)

    def test_a_non_finite_point_is_refused_before_anything_is_compiled(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaises(PointMissionError):
                build_point_mission_spec(north_m=value, east_m=0, altitude_m=3, profile=_profile())


class PointMissionReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.plans = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _review(self, **overrides):
        arguments = {
            "north_m": 20.0,
            "east_m": -10.0,
            "altitude_m": 4.0,
            "goal": "Nézd meg a kert végét, és szólj, ha odaértél.",
            "profile": _profile(),
            "plan_directory": self.plans,
        }
        arguments.update(overrides)
        return review_point_mission(**arguments)

    def test_an_approved_point_lands_on_disk_with_its_approval_proof(self) -> None:
        mission = self._review()

        raw = mission.plan_path.read_bytes()
        require_matching_review_approval(mission.plan_path, raw)  # raises if the proof is missing
        self.assertTrue(mission.plan_path.is_file())
        self.assertIn("22 m-re", mission.summary)

    def test_the_goal_is_recorded_beside_the_plan_never_inside_it(self) -> None:
        mission = self._review(goal="Ellenőrizd a kaput")

        spec = json.loads(mission.plan_path.read_text(encoding="utf-8"))
        record = json.loads(
            mission.plan_path.with_name(f"{mission.plan_path.name}.goal.json").read_text(encoding="utf-8")
        )

        self.assertNotIn("Ellenőrizd", json.dumps(spec, ensure_ascii=False))
        self.assertEqual(record["goal"], "Ellenőrizd a kaput")

    def test_a_point_outside_the_radius_is_refused_and_writes_nothing(self) -> None:
        with self.assertRaisesRegex(PointMissionError, "radius"):
            self._review(north_m=_beyond_the_radius_m())

        self.assertEqual(list(self.plans.iterdir()), [], "an unapproved plan must not exist on disk")

    def test_an_altitude_above_the_ceiling_is_refused(self) -> None:
        with self.assertRaisesRegex(PointMissionError, "altitude"):
            self._review(altitude_m=40.0)

    def test_a_mission_without_a_stated_goal_is_refused(self) -> None:
        with self.assertRaisesRegex(PointMissionError, "goal"):
            self._review(goal="   ")


class PointMissionApiTests(unittest.TestCase):
    """The map page reaches the flight path only through the approval boundary."""

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.executed: list[str] = []
        self.gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "chat-plan",
            execute=lambda plan: self.executed.append(plan) or "submitted",
        )
        self.client = TestClient(create_app(self.root / "telemetry.json", gateway=self.gateway))

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _pick(self, **overrides):
        body = {"north_m": 15.0, "east_m": 5.0, "altitude_m": 4.0, "goal": "Menj oda és jelezz."}
        body.update(overrides)
        return self.client.post(
            "/api/v1/missions/point", json=body, headers={"X-ByteWolf-Session": SESSION}
        )

    def test_picking_a_point_reviews_a_plan_without_flying_anything(self) -> None:
        response = self._pick()

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_required"])
        self.assertEqual(response.json()["steps"], ["TAKEOFF", "GOTO_LOCAL", "HOLD", "RTL"])
        self.assertEqual(self.executed, [], "reviewing is not executing")

    def test_the_reviewed_point_can_then_be_approved_exactly_once(self) -> None:
        plan_id = self._pick().json()["plan_id"]

        approved = self.client.post(
            "/api/v1/plans/approve", json={"plan_id": plan_id}, headers={"X-ByteWolf-Session": SESSION}
        )
        replayed = self.client.post(
            "/api/v1/plans/approve", json={"plan_id": plan_id}, headers={"X-ByteWolf-Session": SESSION}
        )

        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.executed, [plan_id])
        self.assertEqual(replayed.status_code, 409, "the pending slot empties on approval")

    def test_another_session_cannot_approve_this_session_s_point(self) -> None:
        plan_id = self._pick().json()["plan_id"]

        stolen = self.client.post(
            "/api/v1/plans/approve",
            json={"plan_id": plan_id},
            headers={"X-ByteWolf-Session": "0f7b2c62-1a1a-4c2f-9a55-2f9e4a6c1b33"},
        )

        self.assertEqual(stolen.status_code, 409)
        self.assertEqual(self.executed, [])

    def test_a_refused_point_reports_the_constraint_and_stays_unapprovable(self) -> None:
        refused = self._pick(north_m=_beyond_the_radius_m())

        self.assertEqual(refused.status_code, 422)
        self.assertIn("radius", refused.json()["detail"])
        self.assertEqual(self.executed, [])

    def test_the_dashboard_ships_the_mission_page_it_advertises(self) -> None:
        page = self.client.get("/").text

        self.assertIn('data-target="page-mission"', page)
        self.assertIn('id="mission-map"', page)
        self.assertIn("/api/v1/missions/point", page)
        self.assertIn("szabad területet nem állít", page)


if __name__ == "__main__":
    unittest.main()
