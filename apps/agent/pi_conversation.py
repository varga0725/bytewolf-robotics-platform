"""The Pi conversational turn, running on the Python Cognitive Runtime.

This is the retirement of the Node conversational loop: one turn resolved
entirely in Python. It resolves the same briefings the dashboard shows, builds
the ported system prompt, runs the turn on the Cognitive Runtime with the
read-only tool plugins and the reserved draft-flight tool, and updates durable
memory through the cognitive-hooks pipeline.

Boundaries preserved from the Node turn: the tools only read or draft, a flight
is never executed (it is filed for review), the briefings are framed as data not
instructions, and a failed turn degrades to a safe message rather than a crash.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.agent.pi_memory import PiMemoryHook
from apps.agent.pi_prompt import system_prompt
from apps.gateway.memory_store import _load_facts
from apps.gateway.pi_agent import PiAgentReply
from apps.plugins import telemetry_read, vision_summary, world_query
from brain.cognitive_runtime import CognitiveRuntime, Provider, SessionManager
from brain.memory.briefing import capability_briefing, world_briefing
from brain.memory.world_memory import load_world_memory
from brain.plugin_sdk import PluginRegistry, build_tool_policy, load_plugin_manifest
from brain.safety.profile import SafetyProfileError, load_safety_profile


_READ_CAPABILITIES = ("telemetry.read", "vision.summary", "world_memory.query")
_SAFE_FALLBACK = "Elnézést, most nem tudok biztonságosan válaszolni."

_CONSUMER = {
    "contract_version": "v0.1",
    "plugin_id": "pi.agent",
    "version": "0.1.0",
    "name": "Pi Agent",
    "provides": [{"capability_id": "pi.agent.turn", "version": "v0.1", "access": "read"}],
    "requests": [{"capability_id": c, "version": "v0.1"} for c in _READ_CAPABILITIES],
}

# extractor: (user_message, assistant_reply) -> raw memory delta or None
MemoryExtractor = Callable[[str, str], Any]


class PiConversation:
    """Run one dashboard chat turn on the Python Cognitive Runtime."""

    def __init__(
        self,
        provider: Provider,
        *,
        telemetry_path: Path | str,
        detections_path: Path | str,
        twin_path: Path | str,
        world_memory_path: Path | str,
        memory_dir: Path | str,
        extractor: MemoryExtractor | None = None,
        sessions: SessionManager | None = None,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._provider = provider
        self._telemetry_path = Path(telemetry_path)
        self._detections_path = Path(detections_path)
        self._twin_path = Path(twin_path)
        self._world_memory_path = Path(world_memory_path)
        self._memory_dir = Path(memory_dir)
        self._extractor = extractor
        self._sessions = sessions or SessionManager()  # keeps conversation history
        self._memory_hook = PiMemoryHook(memory_dir)
        self._now = now or (lambda: datetime.now(UTC))

    def converse(
        self, session_id: str, text: str, world_context: str = "", capability_context: str = ""
    ) -> PiAgentReply:
        """Run one turn and return the same typed reply the Node runner did."""
        facts = _load_facts(self._memory_dir, session_id)
        capabilities = capability_context.strip() or self._capability_briefing()
        world = world_context.strip() or self._world_briefing()
        prompt = system_prompt(facts, world, capabilities)

        registry = self._registry()
        runtime = CognitiveRuntime(
            self._provider,
            registry,
            prompt_version="cognitive-runtime.pi.v0_1",
            sessions=self._sessions,
            flight_request_handler=_acknowledge_flight_request,
        )
        policy = build_tool_policy(
            load_plugin_manifest(_CONSUMER), registry, allowlist=set(_READ_CAPABILITIES)
        )
        envelope = runtime.run_turn(session_id, text, policy, system_prompt=prompt)

        reply = envelope.reply if envelope.status == "completed" and envelope.reply else _SAFE_FALLBACK
        requests_drone_action = bool(envelope.safety_verdict.get("flight_drafted"))
        memory_update = self._record_memory(session_id, envelope.turn_id, text, reply, envelope.status)
        return PiAgentReply(reply, requests_drone_action, memory_update)

    # -- internals --------------------------------------------------------

    def _registry(self) -> PluginRegistry:
        registry = PluginRegistry()
        telemetry_read.register(registry, self._telemetry_path)
        vision_summary.register(registry, self._detections_path)
        world_query.register(registry, self._world_memory_path)
        for capability in _READ_CAPABILITIES:
            registry.start(_PLUGIN_OF[capability])
        return registry

    def _record_memory(self, session_id: str, turn_id: str, text: str, reply: str, status: str) -> str:
        if self._extractor is None or status != "completed":
            return "unavailable"
        try:
            delta = self._extractor(text, reply)
        except Exception:  # noqa: BLE001 - extractor faults are a skipped memory, not a crash
            return "unavailable"
        return self._memory_hook.record(session_id, turn_id, delta)

    def _capability_briefing(self) -> str:
        try:
            return capability_briefing(load_safety_profile(self._twin_path))
        except SafetyProfileError:
            return ""

    def _world_briefing(self) -> str:
        try:
            memory = load_world_memory(self._world_memory_path)
            now = self._now()
            return world_briefing(memory.recall(now), memory.disputed(now), now=now)
        except (OSError, ValueError):
            return ""


def _acknowledge_flight_request(arguments: dict[str, Any]) -> dict[str, Any]:
    """Signal flight intent for review; do not compile or validate a MissionSpec.

    A chat model cannot author a full MissionSpec, and it must not: the turn only
    records that the user wants to fly, which sets requests_drone_action. The
    gateway's existing review step compiles the natural-language request into a
    MissionSpec, and it still needs explicit approval before anything executes.
    """
    request = arguments.get("request") if isinstance(arguments, dict) else None
    return {"status": "pending_review", "request": request}


_PLUGIN_OF = {
    "telemetry.read": "telemetry.read",
    "vision.summary": "vision.summary",
    "world_memory.query": "world_memory.query",
}
