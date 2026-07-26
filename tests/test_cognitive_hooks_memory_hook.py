"""The post-turn memory hook on the cognitive-hooks runtime.

The Python hook maps outcomes as the retired Node hook's contract required: an
extractor fault -> unavailable, a malformed or empty delta -> skipped, an
admitted fact -> updated. (The cross-runtime parity check against the Node hook
was retired with the Node runner; the parity it proved is now the Python
behaviour these tests assert.)
"""

from __future__ import annotations

import unittest

from brain.cognitive_hooks import HookRuntime, run_post_turn_memory


NOW = "2026-07-23T10:00:00+00:00"


def _run_python(delta, *, raise_it=False) -> str:
    def extract(_payload):
        if raise_it:
            raise RuntimeError("extractor blew up")
        return delta

    return run_post_turn_memory(
        HookRuntime(),
        session_id="session",
        turn_id="turn-1",
        user_message="A nevem Ferenc.",
        assistant_reply="Örülök, Ferenc.",
        extract=extract,
        now=NOW,
    )


class MemoryHookMappingTests(unittest.TestCase):
    def test_extractor_fault_is_unavailable(self) -> None:
        self.assertEqual(_run_python(None, raise_it=True), "unavailable")

    def test_admitted_name_is_updated(self) -> None:
        delta = {"kind": "memory_delta", "operations": [{"op": "upsert", "category": "name", "value": "Ferenc"}]}
        self.assertEqual(_run_python(delta), "updated")

    def test_sensitive_value_is_skipped(self) -> None:
        delta = {"kind": "memory_delta",
                 "operations": [{"op": "upsert", "category": "preference", "value": "a jelszavam titkos123"}]}
        self.assertEqual(_run_python(delta), "skipped")

    def test_wrong_kind_is_skipped_not_unavailable(self) -> None:
        self.assertEqual(_run_python({"kind": "other"}), "skipped")

    def test_unknown_category_is_skipped_not_unavailable(self) -> None:
        delta = {"kind": "memory_delta", "operations": [{"op": "upsert", "category": "credentials", "value": "x"}]}
        self.assertEqual(_run_python(delta), "skipped")

    def test_empty_operations_is_skipped(self) -> None:
        self.assertEqual(_run_python({"kind": "memory_delta", "operations": []}), "skipped")


if __name__ == "__main__":
    unittest.main()
