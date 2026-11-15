"""Exercise the map proposal, operator token and spawn-time home check in process."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.command_audit import plan_sha256
from apps.api.server import create_app
from apps.gateway import plan_execution
from tests.command_approval_helpers import IDENTITY_TEST_ENV, issue_and_approve


class MapMissionApiFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        directory = self.enterContext(TemporaryDirectory())
        self.root = Path(directory)
        self.telemetry = self.root / "configured-telemetry.json"
        self.plans = self.root / "plans"
        self.home = {
            "latitude_deg": 37.416, "longitude_deg": -122.049, "absolute_altitude_m": 0,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        self.headers = {"X-ByteWolf-Session": "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3"}
        self.payload = {
            "home": self.home,
            "waypoints": [
                {
                    "latitude_deg": 37.416 + index * 0.00009, "longitude_deg": -122.049,
                    "relative_altitude_m": 8, "north_m": 9999, "east_m": 9999,
                }
                for index in (1, 2, 3)
            ],
        }
        self.checked_paths: list[Path] = []
        self.write_snapshot(self.home)
        self.enterContext(patch.dict(os.environ, {
            **IDENTITY_TEST_ENV,
            "BYTEWOLF_COMMANDS_ENABLED": "true",
            "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
            "BYTEWOLF_DASHBOARD_TELEMETRY_FILE": str(self.root / "missing-env-snapshot.json"),
        }))
        self.enterContext(patch(
            "brain.mission.demo_waypoint_reviewed_plan.DEFAULT_DEMO_WAYPOINT_PLAN_DIRECTORY", self.plans
        ))
        self.enterContext(patch("apps.api.server.execute_reviewed_plan", side_effect=self.check_before_spawn))
        self.client = self.enterContext(TestClient(create_app(
            self.telemetry, plan_directory=self.plans, command_audit_path=self.root / "audit.jsonl",
        )))

    def write_snapshot(self, home: dict[str, object] | None) -> None:
        document = {
            "position": {"latitude_deg": 37.416, "longitude_deg": -122.049,
                         "absolute_altitude_m": 0, "relative_altitude_m": 0},
            "battery": {"remaining_percent": 99},
            "in_air": False,
            "captured_at": datetime.now(UTC).isoformat(),
        }
        if home is not None:
            document["home"] = home
        self.telemetry.write_text(json.dumps(document), encoding="utf-8")

    def check_before_spawn(self, plan_id: str, *, telemetry_path: Path) -> str:
        self.checked_paths.append(telemetry_path)
        plan_execution._validate_plan_before_spawn(self.plans / plan_id, telemetry_path)
        return "submitted"

    def propose(self):
        return self.client.post("/api/v1/missions/demo-waypoint-mission", headers=self.headers, json=self.payload)

    def test_geographic_plan_hash_token_and_configured_spawn_home(self) -> None:
        proposal = self.propose()
        self.assertEqual(proposal.status_code, 200, proposal.text)
        body = proposal.json()
        path = self.plans / body["plan_id"]
        self.assertEqual(body["plan_hash"], plan_sha256(path))
        stored = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(len(stored["waypoints"]), 3)
        for index, waypoint in enumerate(stored["waypoints"], start=1):
            self.assertAlmostEqual(waypoint["north_m"], 10 * index, delta=0.1)
            self.assertAlmostEqual(waypoint["east_m"], 0)

        rejected = self.client.post("/api/v1/plans/approve", headers=self.headers, json={
            "plan_id": path.name, "approval_token": "invalid-approval-token-12345678",
        })
        self.assertEqual(rejected.status_code, 403)
        self.assertEqual(self.checked_paths, [])

        token, approved = issue_and_approve(self.client, self.headers["X-ByteWolf-Session"], path.name)
        self.assertEqual(token.status_code, 200, token.text)
        self.assertEqual(token.json()["plan_hash"], body["plan_hash"])
        self.assertEqual(approved.status_code, 200, approved.text)
        self.assertEqual(self.checked_paths, [self.telemetry])

    def test_home_drift_is_refused_at_approval(self) -> None:
        proposal = self.propose()
        self.assertEqual(proposal.status_code, 200, proposal.text)
        self.write_snapshot({**self.home, "latitude_deg": 37.41609})
        _, approved = issue_and_approve(
            self.client, self.headers["X-ByteWolf-Session"], proposal.json()["plan_id"]
        )
        self.assertEqual(approved.status_code, 422, approved.text)
        self.assertIn("differs from PX4 home", approved.json()["detail"])

    def test_missing_home_is_refused_at_proposal(self) -> None:
        self.write_snapshot(None)
        response = self.propose()
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("HOME_POSITION", response.json()["detail"])
        self.assertEqual(self.checked_paths, [])
