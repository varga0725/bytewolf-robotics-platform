"""The post-turn memory hook is isolated from the conversation and the flight.

A hook that fails, stalls, or misbehaves may cost the user a remembered fact.
It may never cost them the reply, change a flight decision, or put remembered
text into a diagnostic channel.
"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import unittest

from fastapi.testclient import TestClient

from apps.api.command_gateway import AgentReply, DashboardCommandGateway
from apps.api.server import create_app
from apps.gateway.pi_agent import PiAgentClient, PiAgentError


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PiMemoryHookBoundaryTests(unittest.TestCase):
    """The Python boundary accepts only a status word from the hook."""

    def test_a_failed_hook_still_returns_the_safe_reply(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {
                "text": "Rendben, ezt megnézem.",
                "requests_drone_action": False,
                "memory_update": "unavailable",
            }
        )

        reply = client.converse(SESSION, "szia")

        self.assertEqual(reply.text, "Rendben, ezt megnézem.")
        self.assertEqual(reply.memory_update, "unavailable")

    def test_a_missing_hook_status_is_read_as_a_failed_hook(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {"text": "Szia!", "requests_drone_action": False}
        )

        self.assertEqual(client.converse(SESSION, "szia").memory_update, "unavailable")

    def test_the_hook_channel_cannot_carry_remembered_text(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {
                "text": "Szia!",
                "requests_drone_action": False,
                "memory_update": "updated: a felhasználó neve Ferenc",
            }
        )

        self.assertEqual(client.converse(SESSION, "szia").memory_update, "unavailable")

    def test_a_runner_failure_is_reported_without_echoing_the_conversation(self) -> None:
        secret = "A jelszavam titkos123 és a nevem Ferenc."

        def failing_runner(_request: dict[str, object]) -> dict[str, object]:
            raise RuntimeError(f"NIM rejected: {secret}")

        with self.assertRaises(PiAgentError) as raised:
            PiAgentClient(runner=failing_runner).converse(SESSION, secret)

        self.assertNotIn("jelszavam", str(raised.exception))
        self.assertNotIn("Ferenc", str(raised.exception))
        self.assertIn("the drone received no command", str(raised.exception))

    def test_an_admitted_fact_is_reported_as_a_status_word_only(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {
                "text": "Megjegyeztem.",
                "requests_drone_action": False,
                "memory_update": "updated",
            }
        )

        self.assertEqual(client.converse(SESSION, "A nevem Ferenc.").memory_update, "updated")


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

    def test_the_dashboard_renders_the_hook_status_as_diagnostics(self) -> None:
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "plan-1",
            execute=lambda plan: "submitted",
        )
        page = TestClient(create_app(PROJECT_ROOT / "missing-telemetry.json", gateway=gateway)).get("/").text

        self.assertIn("memory-status", page)
        self.assertIn("memory_update", page)


@unittest.skipUnless(shutil.which("node"), "Node is required for the cross-runtime hook contract.")
class PiMemoryHookRuntimeContractTests(unittest.TestCase):
    """The Node hook and the Python boundary must agree on the same contract.

    The runner itself needs the Pi SDK and a live NIM endpoint, so this drives
    the extracted hook module directly and feeds its output through the exact
    Python boundary the dashboard uses.
    """

    def _hook_status(self, extractor: str) -> str:
        script = (
            "import { runPostTurnMemoryHook, safeMemoryUpdate } from "
            f"'{PROJECT_ROOT / 'apps' / 'pi_agent' / 'post_turn.mjs'}';\n"
            "const status = await runPostTurnMemoryHook({\n"
            f"  extract: {extractor},\n"
            "  loadFacts: async () => [],\n"
            "  saveFacts: async () => {},\n"
            "  now: () => '2026-07-20T10:00:00Z',\n"
            "  sessionId: 'session', turnId: 'turn-1',\n"
            "  userMessage: 'A nevem Ferenc.', assistantReply: 'Örülök, Ferenc.',\n"
            "});\n"
            "process.stdout.write(JSON.stringify({text: 'Örülök!', requests_drone_action: false, "
            "memory_update: safeMemoryUpdate(status)}));\n"
        )
        completed = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30, check=True,
        )
        response = json.loads(completed.stdout)
        return PiAgentClient(runner=lambda _request: response).converse(SESSION, "A nevem Ferenc.").memory_update

    def test_a_failing_extractor_reaches_python_as_an_unavailable_hook(self) -> None:
        status = self._hook_status("async () => { throw new Error('NIM 503 for https://example.test'); }")

        self.assertEqual(status, "unavailable")

    def test_an_admitted_delta_reaches_python_as_an_updated_hook(self) -> None:
        status = self._hook_status(
            "async () => ({kind: 'memory_delta', operations: [{op: 'upsert', category: 'name', value: 'Ferenc'}]})"
        )

        self.assertEqual(status, "updated")


if __name__ == "__main__":
    unittest.main()
