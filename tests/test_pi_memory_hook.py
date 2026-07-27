"""The post-turn memory status is a safe word at the dashboard boundary.

The Node hook and its subprocess runner are retired; the conversational turn and
its memory now run in Python (see tests/test_pi_conversation.py and
tests/test_pi_memory_cutover.py). These tests keep the guarantees that live at
the gateway and dashboard: a hook failure never degrades the conversation or the
approval path, and the dashboard only ever sees a status word, never remembered
text.
"""

from __future__ import annotations

import json
from pathlib import Path
import unittest

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import create_app


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PiMemoryHookGatewayTests(unittest.TestCase):
    """A hook failure must not degrade the conversation or the approval path."""

    def _gateway(self, memory_update: str, *, requests_flight: bool) -> DashboardCommandGateway:
        return DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Rendben.", requests_flight, memory_update),
            review=lambda _text: "plan-1",
            execute=lambda plan: "submitted",
        )

    def test_conversation_survives_a_failed_hook(self) -> None:
        reply = self._gateway("unavailable", requests_flight=False).chat(SESSION, "szia")

        self.assertEqual(reply.status, "conversation")
        self.assertEqual(reply.text, "Rendben.")
        self.assertEqual(reply.memory_update, "unavailable")

    def test_a_failed_hook_does_not_change_the_flight_approval_boundary(self) -> None:
        reply = self._gateway("unavailable", requests_flight=True).chat(SESSION, "repülj a piros jelhez")

        self.assertEqual(reply.status, "awaiting_approval")
        self.assertTrue(reply.approval_required)
        self.assertEqual(reply.plan_id, "plan-1")
        self.assertEqual(reply.memory_update, "unavailable")

    def test_an_unknown_hook_status_is_downgraded_before_it_reaches_the_dashboard(self) -> None:
        reply = self._gateway("Ferenc szereti a Baylands világot", requests_flight=False).chat(SESSION, "szia")

        self.assertEqual(reply.memory_update, "unavailable")


class PiMemoryDiagnosticsApiTests(unittest.TestCase):
    """The dashboard sees a status word, never a remembered fact."""

    def test_chat_exposes_only_a_safe_memory_status(self) -> None:
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Megjegyeztem.", False, "updated"),
            review=lambda _text: "plan-1",
            execute=lambda plan: "submitted",
        )
        client = TestClient(create_app(PROJECT_ROOT / "missing-telemetry.json", gateway=gateway))

        response = client.post(
            "/api/v1/chat", json={"text": "A nevem Ferenc."}, headers={"X-ByteWolf-Session": SESSION}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory_update"], "updated")
        self.assertNotIn("Ferenc", json.dumps(response.json()["memory_update"]))

    def test_the_chat_contract_returns_a_non_sensitive_memory_diagnostic(self) -> None:
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "plan-1",
            execute=lambda plan: "submitted",
        )
        response = TestClient(create_app(PROJECT_ROOT / "missing-telemetry.json", gateway=gateway)).post(
            "/api/v1/chat", json={"text": "Szia"}, headers={"X-ByteWolf-Session": SESSION}
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["memory_update"], "skipped")


if __name__ == "__main__":
    unittest.main()
