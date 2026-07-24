"""Route the live Pi memory update through the cognitive-hooks runtime.

The Node runner now only extracts a proposed memory delta; this is where the
delta is validated, admitted and stored. It runs the delta through the
cognitive-hooks pipeline (``load_proposal`` for validation, ``admit`` for the
deterministic admission decision) and writes the result into the same canonical
store the dashboard memory API reads and edits (``apps/gateway/memory_store``),
in the same ``{facts: [{category, fact, ...}]}`` format, so the two agree by
construction.

It is fail-closed: a malformed or empty delta is ``skipped``, a store or
validation fault is ``unavailable``, and nothing sensitive is ever stored. It has
no flight or actuator path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from apps.gateway.memory_store import _load_facts, _write_facts
from brain.cognitive_hooks import admit, load_proposal
from brain.cognitive_hooks.memory_hook import _well_formed_operations

MAX_MEMORY_ITEMS = 40


class PiMemoryHook:
    """Validate, admit and store one turn's proposed memory delta."""

    def __init__(
        self,
        memory_dir: Path | str,
        *,
        model: str = "unknown",
        prompt_version: str = "memory-hook.v0_2",
        now: Any = None,
    ) -> None:
        self._dir = Path(memory_dir)
        self._model = model
        self._prompt_version = prompt_version
        self._now = now or (lambda: datetime.now(UTC).isoformat())

    def record(self, session_id: str, turn_id: str, delta: Any) -> str:
        """Return one of updated / skipped / unavailable for this delta."""
        try:
            operations = _well_formed_operations(delta)
            if not operations:
                return "skipped"
            created_at = self._now()
            document = {
                "contract_version": "v0.1",
                "proposal_id": turn_id,
                "kind": "memory_delta",
                "source": {
                    "job": "post_turn_memory",
                    "model": self._model,
                    "prompt_version": self._prompt_version,
                    "input_refs": [turn_id],
                },
                "created_at": created_at,
                "payload": {"operations": operations},
            }
            proposal = load_proposal(document)  # validation
            facts = _load_facts(self._dir, session_id)
            known = frozenset((f["category"], _normalize(f.get("fact", ""))) for f in facts)
            result = admit(proposal, known)  # admission
            if not result.accepted:
                return "skipped"
            merged = _merge_canonical(facts, result.accepted, created_at, turn_id)
            _write_facts(self._dir, session_id, merged)
            return "updated"
        except Exception:  # noqa: BLE001 - a fault is 'unavailable', never a raise into the turn
            return "unavailable"


def _merge_canonical(
    facts: list[dict[str, Any]], accepted: tuple[dict[str, str], ...], recorded_at: str, turn_id: str
) -> list[dict[str, Any]]:
    """Apply admitted operations to the shared fact list, matching mergeMemory."""
    result = [dict(fact) for fact in facts]
    for operation in accepted:
        key = (operation["category"], _normalize(operation["value"]))
        if operation["op"] == "forget":
            result = [f for f in result if (f["category"], _normalize(f.get("fact", ""))) != key]
    for index, operation in enumerate(accepted):
        if operation["op"] != "upsert":
            continue
        key = (operation["category"], _normalize(operation["value"]))
        if any((f["category"], _normalize(f.get("fact", ""))) == key for f in result):
            continue
        if operation["category"] == "name":
            result = [f for f in result if f["category"] != "name"]
        result.append({
            "id": f"{turn_id}:{index}",
            "category": operation["category"],
            "fact": operation["value"],
            "recorded_at": recorded_at,
            "source_turn_id": turn_id,
        })
    return result[-MAX_MEMORY_ITEMS:]


def _normalize(value: str) -> str:
    return " ".join(value.split()).lower() if isinstance(value, str) else ""
