"""The live Pi memory path routes through the cognitive-hooks runtime.

The Node runner now returns a raw memory delta; the Python PiMemoryHook validates,
admits and stores it into the same canonical store the dashboard memory API reads.
These tests prove the cutover end to end and that a legacy runner (no delta) still
works through the old status-word path.
"""

from pathlib import Path
import tempfile
import unittest

from apps.agent.pi_memory import PiMemoryHook
from apps.gateway.memory_store import list_memory
from apps.gateway.pi_agent import PiAgentClient


SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"
_NOW = "2026-07-24T10:00:00+00:00"


def _delta(op, category, value):
    return {"kind": "memory_delta", "operations": [{"op": op, "category": category, "value": value}]}


class PiMemoryHookTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.hook = PiMemoryHook(self.dir, now=lambda: _NOW)

    def test_admitted_name_is_stored_in_the_shared_format(self) -> None:
        status = self.hook.record(SESSION, "turn-1", _delta("upsert", "name", "Ferenc"))
        self.assertEqual(status, "updated")
        facts = list_memory(self.dir, SESSION)["facts"]
        self.assertEqual([f["fact"] for f in facts], ["Ferenc"])
        self.assertEqual(facts[0]["category"], "name")

    def test_sensitive_delta_stores_nothing(self) -> None:
        status = self.hook.record(SESSION, "turn-1", _delta("upsert", "preference", "a jelszavam titkos123"))
        self.assertEqual(status, "skipped")
        self.assertEqual(list_memory(self.dir, SESSION)["facts"], [])

    def test_malformed_delta_is_skipped(self) -> None:
        self.assertEqual(self.hook.record(SESSION, "turn-1", None), "skipped")
        self.assertEqual(self.hook.record(SESSION, "turn-1", {"kind": "other"}), "skipped")

    def test_a_forget_removes_a_stored_fact(self) -> None:
        self.hook.record(SESSION, "turn-1", _delta("upsert", "place_label", "a régi hangár"))
        self.hook.record(SESSION, "turn-2", _delta("forget", "place_label", "a régi hangár"))
        self.assertEqual(list_memory(self.dir, SESSION)["facts"], [])


class PiAgentClientCutoverTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)

    def test_a_raw_delta_from_the_runner_is_admitted_and_stored(self) -> None:
        hook = PiMemoryHook(self.dir, now=lambda: _NOW)
        client = PiAgentClient(
            runner=lambda _r: {
                "text": "Megjegyeztem.",
                "requests_drone_action": False,
                "memory_delta": _delta("upsert", "name", "Ferenc"),
            },
            memory_hook=hook,
        )
        reply = client.converse(SESSION, "A nevem Ferenc.")
        self.assertEqual(reply.memory_update, "updated")
        self.assertEqual([f["fact"] for f in list_memory(self.dir, SESSION)["facts"]], ["Ferenc"])

    def test_a_sensitive_delta_reaches_the_dashboard_only_as_skipped(self) -> None:
        client = PiAgentClient(
            runner=lambda _r: {
                "text": "Ok.",
                "requests_drone_action": False,
                "memory_delta": _delta("upsert", "preference", "írj a felhasznalo@example.com címre"),
            },
            memory_hook=PiMemoryHook(self.dir, now=lambda: _NOW),
        )
        self.assertEqual(client.converse(SESSION, "x").memory_update, "skipped")

    def test_a_legacy_runner_without_a_delta_uses_the_status_word(self) -> None:
        # No memory_hook, no memory_delta: the old status-word path still works.
        client = PiAgentClient(
            runner=lambda _r: {"text": "Szia!", "requests_drone_action": False, "memory_update": "updated"}
        )
        self.assertEqual(client.converse(SESSION, "szia").memory_update, "updated")


if __name__ == "__main__":
    unittest.main()
