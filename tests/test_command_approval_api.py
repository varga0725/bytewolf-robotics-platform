"""HTTP approval token and audit integration on the command gateway."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import create_app
from tests.command_approval_helpers import IDENTITY_TEST_ENV, SAMPLE_PLAN_ID, issue_and_approve, seed_plan_file


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class CommandApprovalApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = TemporaryDirectory()
        self.telemetry = Path(self.temp.name) / "telemetry.json"
        self.telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self.audit_path = Path(self.temp.name) / "audit.jsonl"
        self.plan_dir = Path(self.temp.name) / "plans"
        self.executed: list[str] = []
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Tervet készítek.", True),
            review=lambda _text: SAMPLE_PLAN_ID,
            execute=lambda plan_id: self.executed.append(plan_id) or "submitted",
        )
        self._commands_patch = patch.dict(
            os.environ,
            {
                **IDENTITY_TEST_ENV,
                "BYTEWOLF_COMMANDS_ENABLED": "true",
                "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
            },
            clear=False,
        )
        self._commands_patch.start()
        seed_plan_file(self.plan_dir)
        self.client = TestClient(
            create_app(
                self.telemetry,
                plan_directory=self.plan_dir,
                command_audit_path=self.audit_path,
                gateway=gateway,
            )
        )
        headers = {"X-ByteWolf-Session": SESSION}
        self.client.post("/api/v1/chat", headers=headers, json={"text": "repülj"})

    def tearDown(self) -> None:
        self._commands_patch.stop()
        self.temp.cleanup()

    def test_approve_requires_server_token_and_writes_audit(self) -> None:
        headers = {"X-ByteWolf-Session": SESSION}
        token_response, approved = issue_and_approve(self.client, SESSION, SAMPLE_PLAN_ID)
        self.assertEqual(token_response.status_code, 200)
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(self.executed, [SAMPLE_PLAN_ID])
        events = [json.loads(line) for line in self.audit_path.read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(item["event"] == "approve" and item["approved_by"] == "local" for item in events))
        self.assertTrue(all("approval_token" not in line for line in self.audit_path.read_text(encoding="utf-8").splitlines()))

    def test_second_token_issue_after_approval_requires_new_proposal(self) -> None:
        issue_and_approve(self.client, SESSION, SAMPLE_PLAN_ID)
        response = self.client.post(
            "/api/v1/plans/approval-token",
            headers={"X-ByteWolf-Session": SESSION},
            json={"plan_id": SAMPLE_PLAN_ID},
        )
        self.assertEqual(response.status_code, 409)

    def test_wrong_session_cannot_use_another_sessions_token(self) -> None:
        token_response = self.client.post(
            "/api/v1/plans/approval-token",
            headers={"X-ByteWolf-Session": SESSION},
            json={"plan_id": SAMPLE_PLAN_ID},
        )
        other = "0f7b2c62-1a1a-4c2f-9a55-2f9e4a6c1b33"
        approved = self.client.post(
            "/api/v1/plans/approve",
            headers={"X-ByteWolf-Session": other},
            json={
                "plan_id": SAMPLE_PLAN_ID,
                "approval_token": token_response.json()["approval_token"],
            },
        )
        self.assertEqual(approved.status_code, 403)
        self.assertEqual(self.executed, [])

    def test_plan_changed_after_propose_refuses_token_issue(self) -> None:
        path = self.plan_dir / SAMPLE_PLAN_ID
        original = path.read_text(encoding="utf-8")
        token_response = self.client.post(
            "/api/v1/plans/approval-token",
            headers={"X-ByteWolf-Session": SESSION},
            json={"plan_id": SAMPLE_PLAN_ID},
        )
        self.assertEqual(token_response.status_code, 200)
        path.write_text(original + " ", encoding="utf-8")
        blocked = self.client.post(
            "/api/v1/plans/approval-token",
            headers={"X-ByteWolf-Session": SESSION},
            json={"plan_id": SAMPLE_PLAN_ID},
        )
        self.assertEqual(blocked.status_code, 409)

    def test_audit_target_class_uses_telemetry_snapshot(self) -> None:
        sitl = {
            "telemetry": {
                "in_air": False,
                "vehicle_identity": {
                    "sys_autostart": 4001,
                    "sim_gz_en": 1,
                    "product_name": "PX4 SITL",
                    "vendor_name": "PX4",
                    "flight_sw_version_type": "DEV",
                },
            },
        }
        self.telemetry.write_text(json.dumps(sitl), encoding="utf-8")
        client = TestClient(
            create_app(
                self.telemetry,
                plan_directory=self.plan_dir,
                command_audit_path=self.audit_path,
                gateway=DashboardCommandGateway(
                    converse=lambda _s, _t: AgentReply("Tervet készítek.", True),
                    review=lambda _t: SAMPLE_PLAN_ID,
                    execute=lambda plan: self.executed.append(plan) or "submitted",
                ),
            )
        )
        headers = {"X-ByteWolf-Session": SESSION}
        client.post("/api/v1/chat", headers=headers, json={"text": "repülj"})
        issue_and_approve(client, SESSION, SAMPLE_PLAN_ID)
        body = json.loads(self.audit_path.read_text(encoding="utf-8").strip().splitlines()[-1])
        self.assertEqual(body["target_class"], "sitl_verified")


if __name__ == "__main__":
    unittest.main()