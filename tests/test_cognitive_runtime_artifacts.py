"""Coverage for response-envelope audit persistence."""

import json
from pathlib import Path
import tempfile
import unittest

from brain.cognitive_runtime import (
    load_response_envelope,
    persist_envelope,
)


def _envelope_doc():
    return {
        "contract_version": "v0.1",
        "session_id": "s1",
        "turn_id": "s1-1",
        "status": "completed",
        "model": "m",
        "provider": "nim-primary",
        "prompt_version": "test.v0_1",
        "reply": "kész",
        "latency_ms": 12.5,
        "token_usage": {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        "tool_trace": [{"call_id": "c1", "capability_id": "telemetry.read", "status": "ok",
                        "latency_ms": 1.0, "args_ref": "sha256:ab"}],
        "safety_verdict": {"reached_actuation": False, "flight_drafted": False},
    }


class PersistTests(unittest.TestCase):
    def test_persist_writes_a_reloadable_artifact(self) -> None:
        envelope = load_response_envelope(_envelope_doc())
        with tempfile.TemporaryDirectory() as tmp:
            path = persist_envelope(envelope, Path(tmp))
            self.assertTrue(path.exists())
            self.assertEqual(path.name, "s1-1.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertIn("artifact_version", payload)
            # The stored envelope must still satisfy the versioned contract.
            reloaded = load_response_envelope(payload["envelope"])
            self.assertEqual(reloaded.turn_id, "s1-1")
            self.assertEqual(reloaded.token_usage["total_tokens"], 5)
            self.assertEqual(reloaded.tool_trace[0].capability_id, "telemetry.read")
            # No leftover temp file beside the artifact.
            self.assertEqual(sorted(p.name for p in Path(tmp).iterdir()), ["s1-1.json"])


if __name__ == "__main__":
    unittest.main()
