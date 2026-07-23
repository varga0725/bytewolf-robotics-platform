"""The post-turn memory hook, ported onto the cognitive-hooks runtime.

This is the Python counterpart of ``apps/pi_agent/post_turn.mjs``. It runs one
isolated extraction, wraps the resulting memory delta as a proposal, and lets the
same deterministic admission decide what is stored -- returning exactly one of
``updated`` / ``skipped`` / ``unavailable``, and never raising.

The mapping matches the Node hook: an extractor fault is ``unavailable``; a
malformed or empty delta is ``skipped`` (not a failure); an admitted fact is
``updated``. The hook cannot suppress a reply, change a flight decision, or leak
conversation text -- it only proposes durable memory through admission.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from brain.cognitive_hooks.runtime import HookRuntime


MEMORY_UPDATE_STATES = ("updated", "skipped", "unavailable")
ALLOWED_OPERATIONS = frozenset({"upsert", "forget"})
ALLOWED_CATEGORIES = frozenset({"name", "preference", "place_label", "relationship"})

DEFAULT_JOB = "post_turn_memory"
DEFAULT_PROMPT_VERSION = "memory-hook.v0_2"


def run_post_turn_memory(
    runtime: HookRuntime,
    *,
    session_id: str,
    turn_id: str,
    user_message: str,
    assistant_reply: str,
    extract: Callable[[dict[str, str]], Any],
    now: str,
    model: str = "unknown",
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> str:
    """Run one post-turn memory extraction and merge whatever survives admission."""
    try:
        raw = extract({"user_message": user_message, "assistant_reply": assistant_reply})
    except Exception:  # noqa: BLE001 - an extractor fault is 'unavailable', never a raise
        return "unavailable"

    operations = _well_formed_operations(raw)
    if not operations:
        # A malformed or empty delta is a skip, exactly as admitMemoryDelta([]).
        return "skipped"

    document = {
        "contract_version": "v0.1",
        "proposal_id": turn_id,
        "kind": "memory_delta",
        "source": {
            "job": DEFAULT_JOB,
            "model": model,
            "prompt_version": prompt_version,
            "input_refs": [turn_id],
        },
        "created_at": now,
        "payload": {"operations": operations},
    }
    try:
        record = runtime.submit(session_id, document)
    except Exception:  # noqa: BLE001 - a store fault is 'unavailable'
        return "unavailable"

    return record.outcome if record.outcome in MEMORY_UPDATE_STATES else "unavailable"


def _well_formed_operations(raw: Any) -> list[dict[str, str]]:
    """Keep only schema-shaped operations, so a bad one is skipped, not a failure."""
    if not isinstance(raw, dict) or raw.get("kind") != "memory_delta":
        return []
    operations = raw.get("operations")
    if not isinstance(operations, list):
        return []
    clean: list[dict[str, str]] = []
    for operation in operations:
        if (
            isinstance(operation, dict)
            and operation.get("op") in ALLOWED_OPERATIONS
            and operation.get("category") in ALLOWED_CATEGORIES
            and isinstance(operation.get("value"), str)
            and operation["value"] != ""
        ):
            clean.append({"op": operation["op"], "category": operation["category"], "value": operation["value"]})
    return clean
