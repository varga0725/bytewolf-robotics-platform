"""Load and validate the Cognitive Runtime v0.1 contracts, failing closed.

Two versioned documents describe the result of a turn:

* ``ResponseEnvelope`` - the deterministic result of one agent turn. Every turn
  returns exactly one, whatever the outcome, and it asserts (by a ``const false``
  in the schema) that no actuation was reached.
* ``ToolTraceEntry``  - one record of a tool (plugin capability) call, carrying a
  reference to the arguments rather than the raw arguments, so the trace is
  auditable without leaking payloads.

Each loader validates against the frozen JSON Schema with a format checker and
refuses anything it cannot fully trust. See ``docs/workstreams/cognitive-runtime.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import jsonschema


COGNITIVE_RUNTIME_CONTRACT_VERSION = "v0.1"

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "shared/schemas/cognitive_runtime"
RESPONSE_ENVELOPE_SCHEMA_PATH = _SCHEMA_DIR / "response_envelope_v0_1.schema.json"
TOOL_TRACE_SCHEMA_PATH = _SCHEMA_DIR / "tool_trace_v0_1.schema.json"

#: The outcomes a turn may report. A runtime always sets exactly one.
ENVELOPE_STATUSES = frozenset({"completed", "refused", "timeout", "cancelled", "error"})


class ResponseEnvelopeError(ValueError):
    """Raised when a document cannot be read as a Cognitive Runtime contract."""


@dataclass(frozen=True)
class ToolTraceEntry:
    """One tool call: what was invoked, its outcome, and how long it took."""

    call_id: str
    capability_id: str
    status: str
    latency_ms: float
    args_ref: str | None
    detail: str | None


@dataclass(frozen=True)
class ResponseEnvelope:
    """The deterministic result of one agent turn."""

    session_id: str
    turn_id: str
    status: str
    model: str
    prompt_version: str
    latency_ms: float
    tool_trace: tuple[ToolTraceEntry, ...]
    safety_verdict: dict[str, Any]
    reply: str | None
    provider: str | None
    token_usage: dict[str, int] | None
    error: dict[str, str] | None


@lru_cache(maxsize=None)
def _validator(schema_path: Path) -> Any:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ResponseEnvelopeError(
            f"Cannot read the cognitive-runtime schema '{schema_path}': {error.strerror}."
        ) from error
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def _validate(document: object, schema_path: Path, label: str) -> dict[str, Any]:
    try:
        _validator(schema_path).validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ResponseEnvelopeError(f"{label} rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."
    return document


def _tool_trace_entry(document: dict[str, Any]) -> ToolTraceEntry:
    return ToolTraceEntry(
        call_id=document["call_id"],
        capability_id=document["capability_id"],
        status=document["status"],
        latency_ms=float(document["latency_ms"]),
        args_ref=document.get("args_ref"),
        detail=document.get("detail"),
    )


def load_tool_trace_entry(document: object) -> ToolTraceEntry:
    """Read a document as a bare tool trace entry, or refuse it."""
    validated = _validate(document, TOOL_TRACE_SCHEMA_PATH, "ToolTraceEntry")
    return _tool_trace_entry(validated)


def load_response_envelope(document: object) -> ResponseEnvelope:
    """Read a document as a ResponseEnvelope, or refuse it."""
    validated = _validate(document, RESPONSE_ENVELOPE_SCHEMA_PATH, "ResponseEnvelope")
    return ResponseEnvelope(
        session_id=validated["session_id"],
        turn_id=validated["turn_id"],
        status=validated["status"],
        model=validated["model"],
        prompt_version=validated["prompt_version"],
        latency_ms=float(validated["latency_ms"]),
        tool_trace=tuple(_tool_trace_entry(item) for item in validated["tool_trace"]),
        safety_verdict=validated["safety_verdict"],
        reply=validated.get("reply"),
        provider=validated.get("provider"),
        token_usage=validated.get("token_usage"),
        error=validated.get("error"),
    )
