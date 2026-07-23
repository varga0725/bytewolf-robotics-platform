"""Coverage for ToolPolicy rate/concurrency enforcement.

The enforcer is tested directly with an injected clock, and end to end through
the turn loop: a tool the policy rate-limits is refused as a 'denied' trace entry
rather than run.
"""

import unittest

from brain.cognitive_runtime import (
    CognitiveRuntime,
    LimitEnforcer,
    ProviderResponse,
    ToolCall,
)
from brain.plugin_sdk import PluginRegistry, build_tool_policy, load_plugin_manifest


class LimitEnforcerTests(unittest.TestCase):
    def test_rate_window_trips_and_recovers(self) -> None:
        now = [0.0]
        enforcer = LimitEnforcer(rate_per_min=2, clock=lambda: now[0])
        self.assertIsNone(enforcer.acquire())
        enforcer.release()
        self.assertIsNone(enforcer.acquire())
        enforcer.release()
        self.assertIsNotNone(enforcer.acquire())  # third within the window is refused
        now[0] = 61.0
        self.assertIsNone(enforcer.acquire())  # window has rolled past

    def test_concurrency_trips_until_released(self) -> None:
        enforcer = LimitEnforcer(max_concurrent=1)
        self.assertIsNone(enforcer.acquire())
        self.assertIsNotNone(enforcer.acquire())  # second in-flight is refused
        enforcer.release()
        self.assertIsNone(enforcer.acquire())


class _ScriptedProvider:
    name = "scripted"

    def __init__(self, responses):
        self._responses = list(responses)
        self.served_by = "scripted"

    def complete(self, messages, tools):
        return self._responses.pop(0)


class _Plugin:
    def capabilities(self):
        return {"telemetry.read": lambda **k: {"ok": True}}


def _manifest(plugin_id, caps):
    return load_plugin_manifest({
        "contract_version": "v0.1", "plugin_id": plugin_id, "version": "1.0.0", "name": plugin_id,
        "provides": [{"capability_id": c, "version": "v0.1", "access": "read"} for c in caps],
    })


def _consumer(requests):
    return load_plugin_manifest({
        "contract_version": "v0.1", "plugin_id": "agent", "version": "1.0.0", "name": "agent",
        "provides": [{"capability_id": "agent.turn", "version": "v0.1", "access": "read"}],
        "requests": [{"capability_id": c, "version": "v0.1"} for c in requests],
    })


class RateLimitInLoopTests(unittest.TestCase):
    def test_second_call_in_a_turn_is_rate_denied(self) -> None:
        # One response asks for telemetry.read twice; rate_per_min=1 denies the second.
        provider = _ScriptedProvider([
            ProviderResponse(
                content=None,
                tool_calls=(ToolCall("c1", "telemetry.read", {}), ToolCall("c2", "telemetry.read", {})),
                model="m", input_tokens=1, output_tokens=1,
            ),
            ProviderResponse(content="kész", tool_calls=(), model="m", input_tokens=1, output_tokens=1),
        ])
        registry = PluginRegistry()
        registry.register(_manifest("tools", ["telemetry.read"]), _Plugin())
        registry.start("tools")
        runtime = CognitiveRuntime(provider, registry, prompt_version="test.v0_1")
        policy = build_tool_policy(
            _consumer(["telemetry.read"]), registry, allowlist={"telemetry.read"},
            limits={"rate_per_min": 1},
        )
        envelope = runtime.run_turn("s1", "kétszer", policy)
        statuses = [entry.status for entry in envelope.tool_trace]
        self.assertEqual(statuses, ["ok", "denied"])
        self.assertIn("rate limit", envelope.tool_trace[1].detail)


if __name__ == "__main__":
    unittest.main()
