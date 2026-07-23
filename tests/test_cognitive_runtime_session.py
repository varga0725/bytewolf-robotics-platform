"""Coverage for the Cognitive Runtime turn loop.

Every path must return exactly one schema-valid envelope, dispatch tools only
through the ToolPolicy, keep the flight boundary closed, and never leak raw
arguments into the trace. Providers and capabilities are deterministic fakes;
one timeout test uses a real short sleep against a tight per-call budget.
"""

import threading
import time
import unittest

from brain.cognitive_runtime import (
    CognitiveRuntime,
    ProviderError,
    ProviderResponse,
    ToolCall,
)
from brain.plugin_sdk import (
    PluginRegistry,
    build_tool_policy,
    load_plugin_manifest,
)


class _ScriptedProvider:
    """Returns queued ProviderResponses in order; raises when told to."""

    name = "scripted"

    def __init__(self, responses, *, fail=False):
        self._responses = list(responses)
        self._fail = fail
        self.served_by = "scripted"

    def complete(self, messages, tools):
        if self._fail:
            raise ProviderError("scripted failure")
        return self._responses.pop(0)


class _ToolPlugin:
    """Provides telemetry.read and a slow.read that blocks until released."""

    def __init__(self):
        self._release = threading.Event()

    def capabilities(self):
        return {"telemetry.read": self._telemetry, "slow.read": self._slow}

    def _telemetry(self, **kwargs):
        return {"battery_percent": 87.5}

    def _slow(self, **kwargs):
        self._release.wait(timeout=2.0)
        return {"done": True}


def _manifest(plugin_id, caps):
    return load_plugin_manifest(
        {
            "contract_version": "v0.1",
            "plugin_id": plugin_id,
            "version": "1.0.0",
            "name": plugin_id,
            "provides": [{"capability_id": c, "version": "v0.1", "access": "read"} for c in caps],
        }
    )


def _consumer(requests):
    return load_plugin_manifest(
        {
            "contract_version": "v0.1",
            "plugin_id": "agent.runtime",
            "version": "1.0.0",
            "name": "Agent",
            "provides": [{"capability_id": "agent.runtime.turn", "version": "v0.1", "access": "read"}],
            "requests": [{"capability_id": c, "version": "v0.1"} for c in requests],
        }
    )


class TurnLoopTests(unittest.TestCase):
    def _runtime_with_tools(self, caps=("telemetry.read",), *, provider):
        registry = PluginRegistry()
        registry.register(_manifest("tools", caps), _ToolPlugin())
        registry.start("tools")
        runtime = CognitiveRuntime(provider, registry, prompt_version="test.v0_1", clock=lambda: time.monotonic())
        return runtime, registry

    def test_a_tool_call_then_a_reply_completes(self) -> None:
        provider = _ScriptedProvider([
            ProviderResponse(
                content=None,
                tool_calls=(ToolCall("c1", "telemetry.read", {}),),
                model="m", input_tokens=10, output_tokens=2,
            ),
            ProviderResponse(content="Az akku 87.5%.", tool_calls=(), model="m", input_tokens=5, output_tokens=3),
        ])
        runtime, registry = self._runtime_with_tools(provider=provider)
        policy = build_tool_policy(_consumer(["telemetry.read"]), registry, allowlist={"telemetry.read"})

        envelope = runtime.run_turn("s1", "Mennyi az akku?", policy)
        self.assertEqual(envelope.status, "completed")
        self.assertEqual(envelope.reply, "Az akku 87.5%.")
        self.assertEqual(len(envelope.tool_trace), 1)
        self.assertEqual(envelope.tool_trace[0].status, "ok")
        self.assertEqual(envelope.token_usage, {"input_tokens": 15, "output_tokens": 5, "total_tokens": 20})
        self.assertFalse(envelope.safety_verdict["reached_actuation"])
        # The trace references arguments, it does not carry them.
        self.assertTrue(envelope.tool_trace[0].args_ref.startswith("sha256:"))

    def test_a_tool_not_granted_is_denied_not_called(self) -> None:
        provider = _ScriptedProvider([
            ProviderResponse(
                content=None,
                tool_calls=(ToolCall("c1", "telemetry.read", {}),),
                model="m", input_tokens=1, output_tokens=1,
            ),
            ProviderResponse(content="Nem érem el.", tool_calls=(), model="m", input_tokens=1, output_tokens=1),
        ])
        runtime, registry = self._runtime_with_tools(provider=provider)
        policy = build_tool_policy(_consumer([]), registry, allowlist=set())  # grants nothing

        envelope = runtime.run_turn("s2", "Mennyi az akku?", policy)
        self.assertEqual(envelope.status, "completed")
        self.assertEqual(envelope.tool_trace[0].status, "denied")

    def test_provider_error_yields_a_fail_closed_error_envelope(self) -> None:
        runtime, registry = self._runtime_with_tools(provider=_ScriptedProvider([], fail=True))
        policy = build_tool_policy(_consumer([]), registry, allowlist=set())
        envelope = runtime.run_turn("s3", "Hello", policy)
        self.assertEqual(envelope.status, "error")
        self.assertIsNone(envelope.reply)
        self.assertEqual(envelope.error["kind"], "provider_error")

    def test_cancellation_returns_a_cancelled_envelope(self) -> None:
        provider = _ScriptedProvider([ProviderResponse(content="hi", tool_calls=(), model="m", input_tokens=1, output_tokens=1)])
        runtime, registry = self._runtime_with_tools(provider=provider)
        policy = build_tool_policy(_consumer([]), registry, allowlist=set())
        envelope = runtime.run_turn("s4", "Hello", policy, cancelled=lambda: True)
        self.assertEqual(envelope.status, "cancelled")
        self.assertIsNone(envelope.reply)

    def test_a_slow_tool_is_abandoned_on_its_timeout(self) -> None:
        provider = _ScriptedProvider([
            ProviderResponse(
                content=None,
                tool_calls=(ToolCall("c1", "slow.read", {}),),
                model="m", input_tokens=1, output_tokens=1,
            ),
            ProviderResponse(content="megszakítva", tool_calls=(), model="m", input_tokens=1, output_tokens=1),
        ])
        runtime, registry = self._runtime_with_tools(caps=("slow.read",), provider=provider)
        consumer = _consumer(["slow.read"])
        policy = build_tool_policy(
            consumer, registry, allowlist={"slow.read"}, limits={"timeout_ms": 50}
        )
        envelope = runtime.run_turn("s5", "Lassú hívás", policy)
        self.assertEqual(envelope.status, "completed")
        self.assertEqual(envelope.tool_trace[0].status, "timeout")


if __name__ == "__main__":
    unittest.main()
