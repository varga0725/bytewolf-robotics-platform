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
from tests.command_approval_helpers import SAMPLE_PLAN_ID, issue_and_approve, seed_plan_file


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
            {"BYTEWOLF_COMMANDS_ENABLED": "true", "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true"},
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


if __name__ == "__main__":
    unittest.main()