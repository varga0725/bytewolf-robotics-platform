"""The post-turn memory hook, ported onto the cognitive-hooks runtime.

Two things are proven here: the Python hook maps outcomes exactly as the Node
hook's contract requires (extractor fault -> unavailable, malformed/empty ->
skipped, admitted -> updated), and -- where Node is available -- the Python
runtime and the Node hook return the *same* status word for the same input, so
the port carries no functional regression.
"""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import unittest

from brain.cognitive_hooks import HookRuntime, run_post_turn_memory


PROJECT_ROOT = Path(__file__).resolve().parents[1]
POST_TURN = PROJECT_ROOT / "apps" / "pi_agent" / "post_turn.mjs"
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


# Each case: a Node extractor expression and the equivalent Python delta.
_CASES = [
    ("throws", "async () => { throw new Error('boom'); }", (None, True)),
    ("name", "async () => ({kind:'memory_delta',operations:[{op:'upsert',category:'name',value:'Ferenc'}]})",
     ({"kind": "memory_delta", "operations": [{"op": "upsert", "category": "name", "value": "Ferenc"}]}, False)),
    ("sensitive", "async () => ({kind:'memory_delta',operations:[{op:'upsert',category:'preference',value:'a jelszavam titkos123'}]})",
     ({"kind": "memory_delta", "operations": [{"op": "upsert", "category": "preference", "value": "a jelszavam titkos123"}]}, False)),
    ("wrong_kind", "async () => ({kind:'other'})", ({"kind": "other"}, False)),
    ("empty", "async () => ({kind:'memory_delta',operations:[]})",
     ({"kind": "memory_delta", "operations": []}, False)),
    ("bad_category", "async () => ({kind:'memory_delta',operations:[{op:'upsert',category:'credentials',value:'x'}]})",
     ({"kind": "memory_delta", "operations": [{"op": "upsert", "category": "credentials", "value": "x"}]}, False)),
    ("street", "async () => ({kind:'memory_delta',operations:[{op:'upsert',category:'place_label',value:'a Kossuth utca'}]})",
     ({"kind": "memory_delta", "operations": [{"op": "upsert", "category": "place_label", "value": "a Kossuth utca"}]}, False)),
]


@unittest.skipUnless(shutil.which("node"), "Node is required for the cross-runtime parity check.")
class MemoryHookParityTests(unittest.TestCase):
    def _run_node(self, extractor: str) -> str:
        script = (
            f"import {{ runPostTurnMemoryHook, safeMemoryUpdate }} from '{POST_TURN}';\n"
            "const status = await runPostTurnMemoryHook({\n"
            f"  extract: {extractor},\n"
            "  loadFacts: async () => [], saveFacts: async () => {},\n"
            "  now: () => '2026-07-23T10:00:00Z',\n"
            "  sessionId: 'session', turnId: 'turn-1',\n"
            "  userMessage: 'A nevem Ferenc.', assistantReply: 'Örülök, Ferenc.',\n"
            "});\n"
            "process.stdout.write(safeMemoryUpdate(status));\n"
        )
        completed = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=30, check=True,
        )
        return completed.stdout.strip()

    def test_node_and_python_agree_on_every_case(self) -> None:
        for label, extractor_js, (delta, raise_it) in _CASES:
            with self.subTest(case=label):
                node_status = self._run_node(extractor_js)
                python_status = _run_python(delta, raise_it=raise_it)
                self.assertEqual(
                    node_status, python_status,
                    f"case '{label}': node={node_status} python={python_status}",
                )


if __name__ == "__main__":
    unittest.main()
