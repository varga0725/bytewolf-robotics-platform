"""Session manager and turn loop for the Cognitive Runtime.

``CognitiveRuntime.run_turn`` is the heart of the runtime: it drives one agent
turn against a provider, dispatches the model's tool calls to Plugin SDK
capabilities through the registry under a ToolPolicy, and returns exactly one
deterministic ``ResponseEnvelope`` whatever happens -- a reply, a refusal, a
timeout, a cancellation, or a provider error.

Boundaries that hold on every path:

* The ToolPolicy is the gate. A tool the policy did not grant is never called;
  it is recorded as a ``denied`` trace entry, and the model is told so.
* A tool call is bounded by the policy's ``timeout_ms``; an over-running call is
  abandoned with a ``timeout`` trace entry rather than blocking the turn.
* No capability can reach actuation (the Plugin SDK forbids the namespace and
  access class), so ``safety_verdict.reached_actuation`` is always false.
* The trace records arguments only by hash (``args_ref``), never their values.

Rate and concurrency enforcement of the ToolPolicy limits is the next package;
this one enforces the per-call timeout and produces the metrics.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any

from brain.cognitive_runtime.contracts import ResponseEnvelope, load_response_envelope
from brain.cognitive_runtime.limits import LimitEnforcer, limits_of
from brain.cognitive_runtime.providers import Provider, ProviderError, ToolCall
from brain.plugin_sdk import PluginRegistry, PluginRegistryError, ToolPolicy


DEFAULT_TOOL_TIMEOUT_MS = 5000
DEFAULT_MAX_ITERATIONS = 6
DEFAULT_TURN_DEADLINE_S = 60.0

#: The one reserved tool that is not a plugin capability. It drafts a flight
#: request for review; it never runs through the registry and never actuates.
DRAFT_FLIGHT_TOOL = "draft_flight_request"


@dataclass
class Session:
    """One conversation's durable message history."""

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    turns: int = 0


class SessionManager:
    """Holds in-memory sessions keyed by an opaque id."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def get(self, session_id: str) -> Session:
        return self._sessions.setdefault(session_id, Session(session_id))

    def reset(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


class CognitiveRuntime:
    """Runs turns against a provider, enforcing the ToolPolicy and a deadline."""

    def __init__(
        self,
        provider: Provider,
        registry: PluginRegistry,
        prompt_version: str,
        *,
        system_prompt: str | None = None,
        sessions: SessionManager | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_iterations: int = DEFAULT_MAX_ITERATIONS,
        turn_deadline_s: float = DEFAULT_TURN_DEADLINE_S,
        flight_request_handler: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self._provider = provider
        self._registry = registry
        self._prompt_version = prompt_version
        self._system_prompt = system_prompt
        self._sessions = sessions or SessionManager()
        self._clock = clock
        self._max_iterations = max_iterations
        self._turn_deadline_s = turn_deadline_s
        self._executor = ThreadPoolExecutor(max_workers=1)
        self._limiters: dict[str, LimitEnforcer] = {}
        # The one reserved tool that is not a plugin capability: it never runs
        # through the registry and never reaches actuation. The handler routes a
        # drafted request to the reviewed MissionSpec -> SafetyGate -> approval
        # path; a turn can draft a flight, it can never fly one.
        self._flight_request_handler = flight_request_handler

    # -- public API -------------------------------------------------------

    def run_turn(
        self,
        session_id: str,
        user_message: str,
        tool_policy: ToolPolicy,
        *,
        tools: Sequence[dict[str, Any]] | None = None,
        cancelled: Callable[[], bool] = lambda: False,
    ) -> ResponseEnvelope:
        """Run one turn and return exactly one deterministic envelope."""
        session = self._sessions.get(session_id)
        session.turns += 1
        turn_id = f"{session_id}-{session.turns}"
        started = self._clock()
        deadline = started + self._turn_deadline_s

        granted = {grant["capability_id"] for grant in tool_policy.granted}
        tool_specs = list(tools) if tools is not None else _tool_specs(granted)
        if tools is None and self._flight_request_handler is not None:
            tool_specs.append(_flight_request_spec())
        history = self._seed_history(session, user_message)
        trace: list[dict[str, Any]] = []
        tokens_in = tokens_out = 0
        provider_name: str | None = None
        model = "unknown"

        for _ in range(self._max_iterations):
            if cancelled():
                return self._finish(session, turn_id, started, "cancelled", None, trace,
                                    tokens_in, tokens_out, model, provider_name,
                                    error=("cancelled", "The caller cancelled the turn."))
            if self._clock() > deadline:
                return self._finish(session, turn_id, started, "timeout", None, trace,
                                    tokens_in, tokens_out, model, provider_name,
                                    error=("turn_timeout", "The turn exceeded its deadline."))
            try:
                response = self._provider.complete(history, tool_specs)
            except ProviderError as error:
                return self._finish(session, turn_id, started, "error", None, trace,
                                    tokens_in, tokens_out, model, provider_name,
                                    error=("provider_error", str(error)))
            tokens_in += response.input_tokens
            tokens_out += response.output_tokens
            model = response.model
            provider_name = getattr(self._provider, "served_by", None) or self._provider.name

            if not response.tool_calls:
                reply = response.content or ""
                self._commit(session, history, {"role": "assistant", "content": reply})
                return self._finish(session, turn_id, started, "completed", reply, trace,
                                    tokens_in, tokens_out, model, provider_name)

            history.append(_assistant_tool_message(response.tool_calls))
            for call in response.tool_calls:
                if call.capability_id == DRAFT_FLIGHT_TOOL and self._flight_request_handler is not None:
                    entry, tool_message = self._draft_flight(call)
                else:
                    entry, tool_message = self._dispatch(call, tool_policy, granted, deadline)
                trace.append(entry)
                history.append(tool_message)

        return self._finish(session, turn_id, started, "error", None, trace,
                            tokens_in, tokens_out, model, provider_name,
                            error=("tool_loop_exhausted", "The turn made no reply within the iteration budget."))

    # -- internals --------------------------------------------------------

    def _seed_history(self, session: Session, user_message: str) -> list[dict[str, Any]]:
        history = list(session.messages)
        if self._system_prompt and not any(m.get("role") == "system" for m in history):
            history.insert(0, {"role": "system", "content": self._system_prompt})
        history.append({"role": "user", "content": user_message})
        return history

    def _commit(self, session: Session, history: list[dict[str, Any]], reply: dict[str, Any]) -> None:
        # Persist the visible conversation (user + assistant reply); tool-call
        # scaffolding stays within the turn.
        session.messages = [m for m in history if m.get("role") in ("system", "user", "assistant")
                            and not m.get("tool_calls")]
        session.messages.append(reply)

    def _dispatch(
        self, call: ToolCall, tool_policy: ToolPolicy, granted: set[str], deadline: float
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        args_ref = _args_ref(call.arguments)
        base = {"call_id": call.call_id or "call", "capability_id": call.capability_id or "unknown",
                "args_ref": args_ref}

        if call.capability_id not in granted:
            entry = {**base, "status": "denied", "latency_ms": 0.0,
                    "detail": "not granted by ToolPolicy"}
            return entry, _tool_result(call, {"error": "denied: not granted by ToolPolicy"})

        limiter = self._limiter_for(call.capability_id, tool_policy)
        rejection = limiter.acquire()
        if rejection is not None:
            entry = {**base, "status": "denied", "latency_ms": 0.0, "detail": rejection}
            return entry, _tool_result(call, {"error": f"denied: {rejection}"})

        timeout_s = _timeout_s(tool_policy)
        started = self._clock()
        future = self._executor.submit(
            self._registry.invoke, call.capability_id, policy=tool_policy, **call.arguments
        )
        try:
            result = future.result(timeout=timeout_s)
        except FutureTimeout:
            latency = (self._clock() - started) * 1000
            entry = {**base, "status": "timeout", "latency_ms": latency,
                    "detail": f"exceeded {timeout_s * 1000:.0f} ms"}
            return entry, _tool_result(call, {"error": "timeout"})
        except PluginRegistryError as error:
            latency = (self._clock() - started) * 1000
            entry = {**base, "status": "error", "latency_ms": latency, "detail": str(error)}
            return entry, _tool_result(call, {"error": str(error)})
        finally:
            limiter.release()
        latency = (self._clock() - started) * 1000
        entry = {**base, "status": "ok", "latency_ms": latency}
        return entry, _tool_result(call, {"result": result})

    def _draft_flight(self, call: ToolCall) -> tuple[dict[str, Any], dict[str, Any]]:
        """Handle the reserved draft-flight tool: draft only, never actuate."""
        base = {"call_id": call.call_id or "call", "capability_id": DRAFT_FLIGHT_TOOL,
                "args_ref": _args_ref(call.arguments)}
        started = self._clock()
        try:
            outcome = self._flight_request_handler(call.arguments)  # type: ignore[misc]
        except Exception as error:  # noqa: BLE001 - a handler fault is a refused draft, not a crash
            latency = (self._clock() - started) * 1000
            entry = {**base, "status": "error", "latency_ms": latency, "detail": str(error)}
            return entry, _tool_result(call, {"error": "flight request could not be drafted"})
        latency = (self._clock() - started) * 1000
        entry = {**base, "status": "ok", "latency_ms": latency}
        # The handler returns a pending-review descriptor, never an execution.
        return entry, _tool_result(call, {"result": outcome})

    def _limiter_for(self, capability_id: str, tool_policy: ToolPolicy) -> LimitEnforcer:
        limiter = self._limiters.get(capability_id)
        if limiter is None:
            rate, concurrent = limits_of(tool_policy.limits)
            limiter = LimitEnforcer(rate_per_min=rate, max_concurrent=concurrent, clock=self._clock)
            self._limiters[capability_id] = limiter
        return limiter

    def _finish(
        self, session: Session, turn_id: str, started: float, status: str, reply: str | None,
        trace: list[dict[str, Any]], tokens_in: int, tokens_out: int, model: str,
        provider: str | None, error: tuple[str, str] | None = None,
    ) -> ResponseEnvelope:
        document: dict[str, Any] = {
            "contract_version": "v0.1",
            "session_id": session.session_id,
            "turn_id": turn_id,
            "status": status,
            "model": model,
            "prompt_version": self._prompt_version,
            "reply": reply if status == "completed" else None,
            "latency_ms": max(0.0, (self._clock() - started) * 1000),
            "token_usage": {"input_tokens": tokens_in, "output_tokens": tokens_out,
                            "total_tokens": tokens_in + tokens_out},
            "tool_trace": trace,
            "safety_verdict": {
                "reached_actuation": False,
                "flight_drafted": any(
                    e["capability_id"] == DRAFT_FLIGHT_TOOL and e["status"] == "ok" for e in trace
                ),
            },
        }
        if provider is not None:
            document["provider"] = provider
        if error is not None:
            document["error"] = {"kind": error[0], "message": error[1]}
        # Building an invalid envelope is a runtime bug, not a normal outcome:
        # validate so a construction mistake fails loudly in tests.
        return load_response_envelope(document)


def _tool_specs(granted: set[str]) -> list[dict[str, Any]]:
    return [
        {"type": "function",
         "function": {"name": capability_id, "parameters": {"type": "object"}}}
        for capability_id in sorted(granted)
    ]


def _flight_request_spec() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": DRAFT_FLIGHT_TOOL,
            "description": (
                "Draft a flight request for human review. This does NOT fly the "
                "drone: the draft goes to reviewed MissionSpec, the SafetyGate and "
                "an explicit approval before anything can execute."
            ),
            "parameters": {"type": "object"},
        },
    }


def _assistant_tool_message(tool_calls: Sequence[ToolCall]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": call.call_id, "type": "function",
             "function": {"name": call.capability_id, "arguments": json.dumps(call.arguments)}}
            for call in tool_calls
        ],
    }


def _tool_result(call: ToolCall, payload: dict[str, Any]) -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call.call_id, "content": _safe_json(payload)}


def _safe_json(payload: dict[str, Any]) -> str:
    try:
        return json.dumps(payload, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "unserializable tool result"})


def _args_ref(arguments: dict[str, Any]) -> str:
    digest = hashlib.sha256(json.dumps(arguments, sort_keys=True, default=str).encode()).hexdigest()
    return f"sha256:{digest[:12]}"


def _timeout_s(tool_policy: ToolPolicy) -> float:
    limits = tool_policy.limits or {}
    return float(limits.get("timeout_ms", DEFAULT_TOOL_TIMEOUT_MS)) / 1000.0
