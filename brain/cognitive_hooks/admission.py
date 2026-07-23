"""Deterministic admission for cognitive-hook proposals.

The model suggests; this code decides. It applies the same discipline the
post-turn memory hook established: cap the batch size and value length, reject
sensitive data (credentials, tokens, e-mail, phone, precise address), drop
duplicates, and order forgets before upserts. It fails closed -- an
over-budget batch stores nothing -- and records a reason for every rejection so
a denial is auditable, never silent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from brain.cognitive_hooks.contracts import Proposal


MAX_OPERATIONS = 8
MAX_VALUE_LENGTH = 200

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE = re.compile(r"\+?\d[\d\s().-]{6,}\d")
_LONG_DIGITS = re.compile(r"\d{7,}")
_SECRET_WORD = re.compile(
    r"\b(password|passwd|jelsz[oó]|token|api[\s_-]?key|secret|titkos|pin\s*k[oó]d|iban)\b",
    re.IGNORECASE,
)
# A precise address is a street-type word next to a number, in either order
# ("12 Main Street" or "Kossuth utca 12"). Both alternatives require a digit, so
# ordinary text mentioning a street type without a number is not rejected.
_STREET = re.compile(
    r"\d+\s*\.?\s*\w*\s*(utca|[uú]t|t[eé]r|k[oö]r[uú]t|street|avenue|road)\b"
    r"|(utca|[uú]t|t[eé]r|k[oö]r[uú]t|street|avenue|road)\s+\d+",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AdmissionResult:
    """What admission kept, what it rejected and why."""

    outcome: str  # "updated" when anything was accepted, else "skipped"
    accepted: tuple[dict[str, str], ...] = field(default_factory=tuple)
    rejected: tuple[dict[str, str], ...] = field(default_factory=tuple)


def admit(proposal: Proposal, known: frozenset[str] = frozenset()) -> AdmissionResult:
    """Decide which operations of a proposal may be stored.

    ``known`` is the set of already-stored normalized values, so a repeat is
    dropped as a duplicate rather than re-applied.
    """
    if proposal.kind != "memory_delta":
        return AdmissionResult("skipped")

    operations = proposal.payload["operations"]
    if len(operations) > MAX_OPERATIONS:
        return AdmissionResult(
            "skipped",
            rejected=tuple(
                {**op, "reason": f"batch over cap ({MAX_OPERATIONS})"} for op in operations
            ),
        )

    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    seen = set(known)

    # Forgets are applied before upserts, so a supersede within one batch works.
    for operation in sorted(operations, key=lambda op: 0 if op["op"] == "forget" else 1):
        reason = _rejection_reason(operation["value"])
        normalized = _normalize(operation["value"])
        if reason is not None:
            rejected.append({**operation, "reason": reason})
        elif operation["op"] == "upsert" and normalized in seen:
            rejected.append({**operation, "reason": "duplicate"})
        else:
            accepted.append(dict(operation))
            seen.add(normalized)

    return AdmissionResult(
        "updated" if accepted else "skipped",
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


def _rejection_reason(value: str) -> str | None:
    if len(value) > MAX_VALUE_LENGTH:
        return f"value over {MAX_VALUE_LENGTH} chars"
    if _EMAIL.search(value):
        return "contains an e-mail address"
    if _SECRET_WORD.search(value):
        return "contains a credential or token"
    if _STREET.search(value):
        return "contains a precise street address"
    if _PHONE.search(value) or _LONG_DIGITS.search(value):
        return "contains a phone number or long digit sequence"
    return None


def _normalize(value: str) -> str:
    return " ".join(value.lower().split())
