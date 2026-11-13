"""Mission execution quarantine must block every actuation entry point."""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import create_app
from apps.gateway import telegram_mission_gateway
from apps.gateway.telegram_mission_gateway import TelegramGatewayError
from brain.safety import deployment
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from shared.safety.execution_gate import (
    MissionExecutionQuarantinedError,
    default_execution_refusal,
    mission_execution_enabled,
    require_mission_execution_enabled,
)

SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class ExecutionGateUnitTests(unittest.TestCase):
    def test_execution_stays_disabled_by_default(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertFalse(mission_execution_enabled())
            with self.assertRaises(MissionExecutionQuarantinedError) as error:
                require_mission_execution_enabled()
            self.assertEqual(error.exception.refusal.code, "mission_execution_quarantined")

    def test_only_explicit_truthy_values_enable_execution(self) -> None:
        for value in ("1", "true", "TRUE", " yes "):
            with self.subTest(value=value), patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": value}):
                require_mission_execution_enabled()
        for value in ("", "0", "false", "no", "maybe"):
            with self.subTest(value=value), patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": value}):
                with self.assertRaises(MissionExecutionQuarantinedError):
                    require_mission_execution_enabled()

    def test_physical_actuation_release_and_twin_policy_stay_locked(self) -> None:
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        self.assertFalse(deployment._PHYSICAL_ACTUATION_RELEASED)
        self.assertFalse(profile.physical_actuation_enabled)


class ExecutionGateApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.executed: list[str] = []
        self.gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Terv", True),
            review=lambda _text: "plan-quarantine",
            execute=lambda plan: self.executed.append(plan) or "submitted",
        )
        self.client = TestClient(create_app(telemetry, gateway=self.gateway))

    def tearDown(self) -> None:
        self.directory.cleanup()

    @patch.dict(os.environ, {}, clear=True)
    def test_plan_approval_refuses_execution_when_quarantined(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        proposed = self.client.post("/api/v1/chat", headers=headers, json={"text": "repülj"})
        plan_id = proposed.json()["plan_id"]
        response = self.client.post("/api/v1/plans/approve", headers=headers, json={"plan_id": plan_id})
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "mission_execution_quarantined")
        self.assertEqual(self.executed, [])

    @patch.dict(os.environ, {}, clear=True)
    def test_map_point_review_still_works_without_execution(self) -> None:
        body = {"north_m": 5.0, "east_m": 0.0, "altitude_m": 3.0, "goal": "Demo pont."}
        response = self.client.post(
            "/api/v1/missions/point", json=body, headers={"X-ByteWolf-Session": SESSION}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_required"])
        self.assertEqual(self.executed, [])

    @patch.dict(os.environ, {}, clear=True)
    def test_survey_review_still_works_without_execution(self) -> None:
        body = {
            "centre_north_m": 0.0,
            "centre_east_m": 0.0,
            "radius_m": 4.0,
            "spacing_m": 2.0,
            "altitude_m": 3.0,
            "goal": "Felderítés.",
        }
        response = self.client.post(
            "/api/v1/missions/survey", json=body, headers={"X-ByteWolf-Session": SESSION}
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["approval_required"])
        self.assertEqual(self.executed, [])

    @patch.dict(os.environ, {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"}, clear=False)
    def test_plan_approval_executes_when_the_gate_is_open(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        plan_id = self.client.post("/api/v1/chat", headers=headers, json={"text": "repülj"}).json()["plan_id"]
        response = self.client.post("/api/v1/plans/approve", headers=headers, json={"plan_id": plan_id})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.executed, [plan_id])

    @patch.dict(os.environ, {}, clear=True)
    def test_demo_point_rtl_start_refuses_when_quarantined(self) -> None:
        response = self.client.post(
            "/api/v1/missions/demo-point-rtl",
            headers={"X-ByteWolf-Session": SESSION},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["detail"]["code"], "mission_execution_quarantined")


class TelegramExecutionGateTests(unittest.TestCase):
    @patch.dict(os.environ, {}, clear=True)
    def test_telegram_execute_refuses_when_quarantined(self) -> None:
        with self.assertRaises(TelegramGatewayError) as error:
            telegram_mission_gateway._execute_with_cli("missing-plan.json")
        self.assertIn("quarantined", str(error.exception).lower())


if __name__ == "__main__":
    unittest.main()
