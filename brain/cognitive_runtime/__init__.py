"""ByteWolf Cognitive Runtime v0.3.

A supervised agent runtime replacing the subprocess-per-turn Pi Agent while
keeping the existing safety boundary: session management, timeout/cancellation,
structured tool trace, token/latency metrics, provider fallback with a circuit
breaker, and a deterministic response envelope. The Pi Agent becomes this
runtime's first adapter and reference harness.

Safety boundary: no runtime tool reaches the MAVSDK/PX4 execution API. Flight is
only requestable via ``draft_flight_request`` -> reviewed MissionSpec ->
SafetyGate -> explicit dashboard approval.

See ``docs/workstreams/cognitive-runtime.md`` for the versioned-contract plan,
the Definition of Done and the acceptance criteria.
"""

from brain.cognitive_runtime.contracts import (
    COGNITIVE_RUNTIME_CONTRACT_VERSION,
    ENVELOPE_STATUSES,
    ResponseEnvelope,
    ResponseEnvelopeError,
    ToolTraceEntry,
    load_response_envelope,
    load_tool_trace_entry,
)

CONTRACT_VERSION = COGNITIVE_RUNTIME_CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "COGNITIVE_RUNTIME_CONTRACT_VERSION",
    "ENVELOPE_STATUSES",
    "ResponseEnvelope",
    "ResponseEnvelopeError",
    "ToolTraceEntry",
    "load_response_envelope",
    "load_tool_trace_entry",
]
