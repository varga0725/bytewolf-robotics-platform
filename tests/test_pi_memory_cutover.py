"""The live Pi memory path routes through the cognitive-hooks runtime.

The Python extractor proposes a memory delta; PiMemoryHook validates, admits and
stores it into the same canonical store the dashboard memory API reads and edits.
These tests prove that pipeline: an admitted fact is stored, a sensitive one is
skipped, a malformed delta is skipped, and a forget removes a stored fact.
"""

from pathlib import Path
import tempfile
import unittest

from apps.agent.pi_memory import PiMemoryHook
from apps.gateway.memory_store import list_memory


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


if __name__ == "__main__":
    unittest.main()
