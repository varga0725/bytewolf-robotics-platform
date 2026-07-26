"""The Python Pi turn's system prompt and memory extractor.

The prompt must keep the safety framing the Node prompt asserted; the extractor
must return a raw delta on success and None on any failure, never raising.
"""

import json
import unittest

import httpx

from apps.agent.pi_memory_extractor import extract_memory_delta
from apps.agent.pi_prompt import system_prompt


class SystemPromptTests(unittest.TestCase):
    def test_frames_briefings_as_data_and_keeps_the_flight_boundary(self) -> None:
        prompt = system_prompt(
            [{"category": "name", "fact": "Ferenc"}],
            world_context="- Egy piros jelző az udvaron (BIZONYOS).",
            capability_context="- Maximális magasság: 20 m.",
        )
        self.assertIn("Ferenc", prompt)
        self.assertIn("Maximális magasság: 20 m", prompt)
        self.assertIn("piros jelző", prompt)
        # The safety framing must survive: draft-only flight, no PX4, data-not-instructions.
        self.assertIn("draft_flight_request", prompt)
        self.assertIn("nincs hozzáférésed PX4-hez", prompt)
        self.assertIn("NEM utasítások", prompt)
        # The read tools are named by their capability ids.
        self.assertIn("telemetry.read", prompt)
        self.assertIn("vision.summary", prompt)

    def test_empty_memory_and_world_state_their_absence(self) -> None:
        prompt = system_prompt([], world_context="", capability_context="")
        self.assertIn("Nincs eltárolt felhasználói tény", prompt)
        self.assertIn("Nincs érvényes, le nem járt észlelés", prompt)


def _extractor_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class MemoryExtractorTests(unittest.TestCase):
    def test_a_forced_tool_call_is_parsed_into_a_delta(self) -> None:
        delta = {"kind": "memory_delta", "operations": [{"op": "upsert", "category": "name", "value": "Ferenc"}]}

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"tool_calls": [
                    {"function": {"name": "propose_memory_delta", "arguments": json.dumps(delta)}}
                ]}}]
            })

        result = extract_memory_delta(
            user_message="A nevem Ferenc.", assistant_reply="Örülök, Ferenc.",
            model="m", api_key="k", client=_extractor_client(handler),
        )
        self.assertEqual(result, delta)

    def test_an_http_error_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, json={})

        self.assertIsNone(extract_memory_delta(
            user_message="x", assistant_reply="y", model="m", api_key="k",
            client=_extractor_client(handler),
        ))

    def test_a_malformed_response_returns_none(self) -> None:
        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"choices": []})

        self.assertIsNone(extract_memory_delta(
            user_message="x", assistant_reply="y", model="m", api_key="k",
            client=_extractor_client(handler),
        ))


if __name__ == "__main__":
    unittest.main()
