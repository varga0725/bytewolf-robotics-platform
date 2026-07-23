"""The background-hook runtime: proposal -> validation -> admission -> store.

``HookRuntime.submit`` is the whole pipeline in one call. It validates a raw
document against the proposal contract, runs deterministic admission against what
the session already holds, applies the accepted operations to a canonical store,
and returns an auditable record. It fails closed on every branch: a malformed
document, or one admission rejects wholesale, stores nothing and says so.

Nothing here can reach the flight stack, draft a mission, or command an actuator;
a hook only ever updates durable session memory through admission.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brain.cognitive_hooks.admission import AdmissionResult, admit
from brain.cognitive_hooks.contracts import ProposalContractError, load_proposal


@dataclass(frozen=True)
class AdmissionRecord:
    """The audited result of one submission."""

    proposal_id: str | None
    outcome: str  # "updated" | "skipped" | "unavailable"
    accepted: tuple[dict[str, str], ...] = field(default_factory=tuple)
    rejected: tuple[dict[str, str], ...] = field(default_factory=tuple)
    detail: str | None = None


#: The most durable facts a session keeps, matching the Node hook's cap.
MAX_MEMORY_ITEMS = 40


class ProposalStore:
    """Per-session canonical memory plus an admission audit trail.

    Merge semantics mirror the Node hook's ``mergeMemory``: dedup by
    ``(category, value)``, a new ``name`` supersedes any earlier name, and the
    store keeps at most the most recent ``MAX_MEMORY_ITEMS`` facts.
    """

    def __init__(self) -> None:
        self._facts: dict[str, list[dict[str, str]]] = {}
        self._audit: dict[str, list[AdmissionRecord]] = {}

    def facts(self, session_id: str) -> tuple[dict[str, str], ...]:
        return tuple(self._facts.get(session_id, []))

    def audit(self, session_id: str) -> tuple[AdmissionRecord, ...]:
        return tuple(self._audit.get(session_id, []))

    def known_keys(self, session_id: str) -> frozenset[tuple[str, str]]:
        return frozenset(
            (fact["category"], _normalize(fact["value"]))
            for fact in self._facts.get(session_id, [])
        )

    def apply(self, session_id: str, result: AdmissionResult) -> None:
        facts = self._facts.setdefault(session_id, [])
        for operation in result.accepted:
            key = (operation["category"], _normalize(operation["value"]))
            if operation["op"] == "forget":
                facts[:] = [f for f in facts if (f["category"], _normalize(f["value"])) != key]
                continue
            # upsert
            if any((f["category"], _normalize(f["value"])) == key for f in facts):
                continue
            if operation["category"] == "name":
                facts[:] = [f for f in facts if f["category"] != "name"]
            facts.append({"category": operation["category"], "value": operation["value"]})
        # Keep only the most recent facts, as the Node hook does.
        if len(facts) > MAX_MEMORY_ITEMS:
            del facts[:-MAX_MEMORY_ITEMS]

    def record(self, session_id: str, record: AdmissionRecord) -> None:
        self._audit.setdefault(session_id, []).append(record)


class HookRuntime:
    """Runs the submit pipeline over a proposal store, failing closed."""

    def __init__(self, store: ProposalStore | None = None) -> None:
        self._store = store or ProposalStore()

    @property
    def store(self) -> ProposalStore:
        return self._store

    def submit(self, session_id: str, document: object) -> AdmissionRecord:
        """Validate, admit and store a proposal; return an audited record."""
        try:
            proposal = load_proposal(document)
        except ProposalContractError as error:
            record = AdmissionRecord(None, "unavailable", detail=str(error))
            self._store.record(session_id, record)
            return record

        result = admit(proposal, known=self._store.known_keys(session_id))
        self._store.apply(session_id, result)
        record = AdmissionRecord(
            proposal_id=proposal.proposal_id,
            outcome=result.outcome,
            accepted=result.accepted,
            rejected=result.rejected,
        )
        self._store.record(session_id, record)
        return record


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())
