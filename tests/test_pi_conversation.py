"""The Pi conversational turn running on the Python Cognitive Runtime.

Proves the retirement path end to end: a read turn uses the plugins and updates
memory, a flight-intent turn drafts for review without actuating, and a provider
failure degrades to a safe message. The provider and extractor are fakes.
"""

import json
from pathlib import Path
import tempfile
import unittest

from apps.agent.pi_conversation import PiConversation
from apps.gateway.memory_store import list_memory
from brain.cognitive_runtime import ProviderResponse, ToolCall


ROOT = Path(__file__).resolve().parents[1]
TWIN = ROOT / "shared/config/x500v2/twin.yaml"
SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.served_by = "scripted"
        self.last_messages = None

    def complete(self, messages, tools):
        self.last_messages = messages
        return self._responses.pop(0)


class PiConversationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.telemetry = base / "telemetry.json"
        self.telemetry.write_text(json.dumps({"telemetry": {"battery": {"remaining_percent": 87.5}}}), encoding="utf-8")
        self.detections = base / "detections.json"
        self.detections.write_text(json.dumps({"contract_version": "v0.1", "validity": "missing", "detections": []}), encoding="utf-8")
        self.world = base / "claims.jsonl"
        self.memory = base / "memory"

    def _conversation(self, provider, extractor=None):
        return PiConversation(
            provider,
            telemetry_path=self.telemetry, detections_path=self.detections, twin_path=TWIN,
            world_memory_path=self.world, memory_dir=self.memory,
            sessions_dir=self.memory.parent / "sessions", extractor=extractor,
        )

    def test_a_read_turn_replies_and_updates_memory(self) -> None:
        provider = _ScriptedProvider([
            ProviderResponse(content=None, tool_calls=(ToolCall("c1", "telemetry.read", {}),),
                            model="m", input_tokens=1, output_tokens=1),
            ProviderResponse(content="Az akku 87.5%.", tool_calls=(), model="m", input_tokens=1, output_tokens=1),
        ])
        extractor = lambda _u, _r: {"kind": "memory_delta", "operations": [{"op": "upsert", "category": "name", "value": "Ferenc"}]}
        reply = self._conversation(provider, extractor).converse(SESSION, "Mennyi az akku? A nevem Ferenc.")

        self.assertEqual(reply.text, "Az akku 87.5%.")
        self.assertFalse(reply.requests_drone_action)
        self.assertEqual(reply.memory_update, "updated")
        self.assertEqual([f["fact"] for f in list_memory(self.memory, SESSION)["facts"]], ["Ferenc"])
        # The ported system prompt (with its safety framing) reached the model.
        system = [m for m in provider.last_messages if m.get("role") == "system"]
        self.assertTrue(system and "ByteWolf" in system[0]["content"])

    def test_a_flight_intent_turn_signals_a_request_without_actuating(self) -> None:
        # The turn only signals flight intent; the gateway's review step compiles
        # the natural-language request and still requires explicit approval. The
        # turn never writes a plan and never actuates.
        provider = _ScriptedProvider([
            ProviderResponse(content=None,
                            tool_calls=(ToolCall("c1", "draft_flight_request", {"request": "Repülj egy kört."}),),
                            model="m", input_tokens=1, output_tokens=1),
            ProviderResponse(content="Beküldtem jóváhagyásra.", tool_calls=(), model="m", input_tokens=1, output_tokens=1),
        ])
        reply = self._conversation(provider).converse(SESSION, "Repülj egy kört!")
        self.assertEqual(reply.text, "Beküldtem jóváhagyásra.")
        self.assertTrue(reply.requests_drone_action)

    def test_conversation_history_survives_a_new_process(self) -> None:
        sessions_dir = self.memory.parent / "sessions"

        def _make(provider):
            return PiConversation(
                provider, telemetry_path=self.telemetry, detections_path=self.detections,
                twin_path=TWIN, world_memory_path=self.world, memory_dir=self.memory,
                sessions_dir=sessions_dir,
            )

        first = _ScriptedProvider([
            ProviderResponse(content="Első válasz.", tool_calls=(), model="m", input_tokens=1, output_tokens=1)
        ])
        _make(first).converse(SESSION, "Első kérdés.")

        # A fresh PiConversation (new in-memory sessions) with the same directory
        # -- as after a server restart -- must still see the prior exchange.
        second = _ScriptedProvider([
            ProviderResponse(content="Második.", tool_calls=(), model="m", input_tokens=1, output_tokens=1)
        ])
        _make(second).converse(SESSION, "Második kérdés.")

        contents = [m.get("content") for m in second.last_messages]
        self.assertIn("Első kérdés.", contents)
        self.assertIn("Első válasz.", contents)

    def test_a_provider_failure_degrades_to_a_safe_message(self) -> None:
        class _Dead:
            name = "dead"
            served_by = "dead"

            def complete(self, messages, tools):
                from brain.cognitive_runtime import ProviderError
                raise ProviderError("down")

        reply = self._conversation(_Dead()).converse(SESSION, "szia")
        self.assertIn("nem tudok biztonságosan", reply.text)
        self.assertEqual(reply.memory_update, "unavailable")


if __name__ == "__main__":
    unittest.main()
