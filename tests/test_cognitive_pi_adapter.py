"""Integration coverage for the Pi Agent as a Cognitive Runtime adapter.

Spans the runtime, the Plugin SDK read-only capability, the reserved draft-flight
tool and the real MissionSpec validation: a turn reads state and drafts a flight
for review, a valid draft is filed as a pending plan, an unsafe draft is refused
by the same validation the CLIs use, and no path reaches actuation.
"""

import json
from pathlib import Path
import tempfile
import unittest

from apps.agent.cognitive_pi import build_pi_runtime
from brain.cognitive_runtime import ProviderResponse, ToolCall, persist_envelope


ROOT = Path(__file__).resolve().parents[1]
TWIN = ROOT / "shared/config/x500v2/twin.yaml"

_SNAPSHOT = {"telemetry": {"battery": {"remaining_percent": 87.5}, "in_air": False}}

_VALID_SPEC = {
    "schema_version": "0.1",
    "mission_id": "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3",
    "vehicle_id": "x500v2_reference_01",
    "intent": "test_flight",
    "constraints": {
        "max_altitude_m": 10.0, "max_speed_m_s": 3.0, "max_radius_m": 25.0,
        "minimum_battery_percent_to_start": 40.0, "loss_of_link_action": "RTL",
    },
    "steps": [
        {"type": "TAKEOFF", "altitude_m": 2.0},
        {"type": "GOTO_LOCAL", "north_m": 5.0, "east_m": 0.0, "down_m": -2.0},
        {"type": "HOLD", "duration_s": 3.0},
        {"type": "RTL"},
    ],
    "abort_policy": {"on_timeout": "LAND", "on_low_battery": "RTL", "on_position_invalid": "LAND"},
}


def _unsafe_spec():
    spec = json.loads(json.dumps(_VALID_SPEC))
    spec["constraints"]["max_altitude_m"] = 999.0  # far over the platform ceiling
    return spec


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.served_by = "scripted"

    def complete(self, messages, tools):
        return self._responses.pop(0)


class PiAdapterTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.telemetry = Path(self._tmp.name) / "telemetry.json"
        self.telemetry.write_text(json.dumps(_SNAPSHOT), encoding="utf-8")
        self.pending = Path(self._tmp.name) / "pending"

    def _runtime(self, responses):
        return build_pi_runtime(
            _ScriptedProvider(responses), self.telemetry, TWIN, self.pending
        )

    def test_a_read_turn_uses_the_telemetry_capability(self) -> None:
        runtime, policy = self._runtime([
            ProviderResponse(content=None, tool_calls=(ToolCall("c1", "telemetry.read", {}),),
                            model="m", input_tokens=1, output_tokens=1),
            ProviderResponse(content="Az akku 87.5%.", tool_calls=(), model="m",
                            input_tokens=1, output_tokens=1),
        ])
        envelope = runtime.run_turn("s1", "Mennyi az akku?", policy)
        self.assertEqual(envelope.status, "completed")
        self.assertEqual(envelope.tool_trace[0].capability_id, "telemetry.read")
        self.assertEqual(envelope.tool_trace[0].status, "ok")
        self.assertFalse(envelope.safety_verdict["reached_actuation"])

    def test_a_valid_draft_is_filed_pending_review_not_flown(self) -> None:
        runtime, policy = self._runtime([
            ProviderResponse(content=None,
                            tool_calls=(ToolCall("c1", "draft_flight_request", {"mission_spec": _VALID_SPEC}),),
                            model="m", input_tokens=1, output_tokens=1),
            ProviderResponse(content="Beküldtem jóváhagyásra.", tool_calls=(), model="m",
                            input_tokens=1, output_tokens=1),
        ])
        envelope = runtime.run_turn("s2", "Repülj egy kört!", policy)
        self.assertTrue(envelope.safety_verdict["flight_drafted"])
        self.assertFalse(envelope.safety_verdict["reached_actuation"])
        # A pending plan was written, but WITHOUT an approval record, so the
        # executor would refuse to fly it. This is the whole safety point.
        plans = list(self.pending.glob("*.mission-spec.json"))
        self.assertEqual(len(plans), 1)
        self.assertEqual(list(self.pending.glob("*.approval.json")), [])

        # The audit artifact round-trips through the versioned contract.
        with tempfile.TemporaryDirectory() as adir:
            path = persist_envelope(envelope, Path(adir))
            self.assertTrue(path.exists())

    def test_an_unsafe_draft_is_refused_by_validation_and_files_nothing(self) -> None:
        runtime, policy = self._runtime([
            ProviderResponse(content=None,
                            tool_calls=(ToolCall("c1", "draft_flight_request", {"mission_spec": _unsafe_spec()}),),
                            model="m", input_tokens=1, output_tokens=1),
            ProviderResponse(content="A kérést a biztonsági ellenőrzés elutasította.", tool_calls=(),
                            model="m", input_tokens=1, output_tokens=1),
        ])
        envelope = runtime.run_turn("s3", "Szállj 999 méterre!", policy)
        self.assertFalse(envelope.safety_verdict["reached_actuation"])
        # A rejected draft is recorded as denied, not drafted, and files nothing.
        self.assertEqual(envelope.tool_trace[0].status, "denied")
        self.assertFalse(envelope.safety_verdict["flight_drafted"])
        self.assertFalse(self.pending.exists() and list(self.pending.glob("*.mission-spec.json")))


if __name__ == "__main__":
    unittest.main()
