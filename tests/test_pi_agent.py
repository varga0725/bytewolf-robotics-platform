"""The dashboard's Pi boundary is typed and never grants flight control."""

from __future__ import annotations

import unittest

from apps.gateway.pi_agent import PiAgentClient, PiAgentError


class PiAgentClientTests(unittest.TestCase):
    def test_forwards_a_session_bound_conversation_to_the_runner(self) -> None:
        requests: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: requests.append(request) or {
                "text": "Szia! Mire vagy kíváncsi?",
                "requests_drone_action": False,
            }
        )

        reply = client.converse("browser-session", "csáó")

        self.assertEqual(reply.text, "Szia! Mire vagy kíváncsi?")
        self.assertFalse(reply.requests_drone_action)
        self.assertEqual(requests, [{"session_id": "browser-session", "text": "csáó"}])

    def test_rejects_a_malformed_runner_response_without_falling_back_to_flight(self) -> None:
        client = PiAgentClient(runner=lambda _request: {"text": "hiányzik a jelző"})

        with self.assertRaisesRegex(PiAgentError, "invalid"):
            client.converse("browser-session", "repülj")

    def test_rejects_an_oversized_agent_message(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {"text": "x" * 2_001, "requests_drone_action": False}
        )

        with self.assertRaisesRegex(PiAgentError, "invalid"):
            client.converse("browser-session", "szia")


if __name__ == "__main__":
    unittest.main()
