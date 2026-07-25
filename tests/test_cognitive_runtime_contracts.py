"""Coverage for the Cognitive Runtime v0.1 contracts.

The envelope's job is to be the one deterministic result of a turn, so these
tests are mostly refusals: a completed turn with no reply, an error turn that
still carries one, a safety verdict claiming actuation was reached, a future
version, and a tool trace smuggling output through an ok entry's detail.
"""

import json
from pathlib import Path
import unittest

from brain.cognitive_runtime import (
    ResponseEnvelopeError,
    load_response_envelope,
    load_tool_trace_entry,
)
from brain.cognitive_runtime.contracts import (
    TOOL_TRACE_SCHEMA_PATH,
    _validator,
)


EXAMPLES = Path(__file__).resolve().parents[1] / "shared/interfaces/cognitive_runtime/examples"

_LOADERS = {
    "envelope_": load_response_envelope,
    "tool_trace_": load_tool_trace_entry,
}


def _loader_for(path: Path):
    for prefix, loader in _LOADERS.items():
        if path.name.startswith(prefix):
            return loader
    raise AssertionError(f"fixture '{path.name}' has no known contract prefix")


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self) -> None:
        fixtures = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(fixtures)
        for path in fixtures:
            with self.subTest(fixture=path.name):
                _loader_for(path)(_read(path))

    def test_every_invalid_fixture_is_rejected(self) -> None:
        fixtures = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(fixtures)
        for path in fixtures:
            with self.subTest(fixture=path.name):
                with self.assertRaises(ResponseEnvelopeError):
                    _loader_for(path)(_read(path))


class SafetyInvariantTests(unittest.TestCase):
    def test_reached_actuation_true_is_rejected(self) -> None:
        # The schema pins reached_actuation to false; a turn cannot claim it acted.
        envelope = _read(EXAMPLES / "valid/envelope_completed.v0_1.json")
        envelope["safety_verdict"]["reached_actuation"] = True
        with self.assertRaises(ResponseEnvelopeError):
            load_response_envelope(envelope)

    def test_cognitive_runtime_never_imports_flight_control(self) -> None:
        package = Path(__file__).resolve().parents[1] / "brain/cognitive_runtime"
        for source in package.rglob("*.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)

    def test_completed_requires_a_reply_and_others_forbid_one(self) -> None:
        base = _read(EXAMPLES / "valid/envelope_completed.v0_1.json")
        base["reply"] = None
        with self.assertRaises(ResponseEnvelopeError):
            load_response_envelope(base)

        err = _read(EXAMPLES / "valid/envelope_error.v0_1.json")
        err["reply"] = "should be null on error"
        with self.assertRaises(ResponseEnvelopeError):
            load_response_envelope(err)


class CompatibilityTests(unittest.TestCase):
    def test_future_version_is_rejected(self) -> None:
        envelope = _read(EXAMPLES / "valid/envelope_completed.v0_1.json")
        envelope["contract_version"] = "v0.2"
        with self.assertRaises(ResponseEnvelopeError):
            load_response_envelope(envelope)

    def test_embedded_and_standalone_tool_trace_agree(self) -> None:
        trace_validator = _validator(TOOL_TRACE_SCHEMA_PATH)
        envelope = load_response_envelope(_read(EXAMPLES / "valid/envelope_completed.v0_1.json"))
        self.assertTrue(envelope.tool_trace)
        for entry in envelope.tool_trace:
            document = {
                "call_id": entry.call_id,
                "capability_id": entry.capability_id,
                "status": entry.status,
                "latency_ms": entry.latency_ms,
            }
            if entry.args_ref is not None:
                document["args_ref"] = entry.args_ref
            trace_validator.validate(document)


if __name__ == "__main__":
    unittest.main()
