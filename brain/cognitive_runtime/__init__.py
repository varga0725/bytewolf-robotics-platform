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
from brain.cognitive_runtime.providers import (
    AllProvidersFailedError,
    CircuitBreaker,
    FallbackProvider,
    NIMProvider,
    Provider,
    ProviderError,
    ProviderResponse,
    RetryingProvider,
    ToolCall,
)
from brain.cognitive_runtime.limits import LimitEnforcer
from brain.cognitive_runtime.session import (
    DRAFT_FLIGHT_TOOL,
    CognitiveRuntime,
    Session,
    SessionManager,
)
from brain.cognitive_runtime.artifacts import (
    ARTIFACT_VERSION,
    envelope_to_dict,
    persist_envelope,
)

CONTRACT_VERSION = COGNITIVE_RUNTIME_CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "COGNITIVE_RUNTIME_CONTRACT_VERSION",
    "ENVELOPE_STATUSES",
    "ARTIFACT_VERSION",
    "DRAFT_FLIGHT_TOOL",
    "AllProvidersFailedError",
    "CircuitBreaker",
    "CognitiveRuntime",
    "FallbackProvider",
    "LimitEnforcer",
    "NIMProvider",
    "Provider",
    "ProviderError",
    "ProviderResponse",
    "ResponseEnvelope",
    "ResponseEnvelopeError",
    "RetryingProvider",
    "Session",
    "SessionManager",
    "ToolCall",
    "ToolTraceEntry",
    "envelope_to_dict",
    "load_response_envelope",
    "load_tool_trace_entry",
    "persist_envelope",
]
