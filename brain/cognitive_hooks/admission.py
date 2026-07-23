"""Deterministic admission for cognitive-hook proposals.

The model suggests; this code decides. It mirrors the rules the Node post-turn
memory hook (``apps/pi_agent/memory.mjs``) established, so the Python runtime and
the Node hook reach the same decision: keep at most ``MAX_OPERATIONS`` operations,
cap value length, reject sensitive data (credentials, tokens, e-mail, phone,
card, precise address), drop duplicates by (category, value), and order forgets
before upserts. It fails closed and records a reason for every rejection.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any

from brain.cognitive_hooks.contracts import Proposal


# Kept identical to apps/pi_agent/memory.mjs so the two runtimes agree.
MAX_OPERATIONS = 6
MAX_VALUE_LENGTH = 240

# A single sensitive-data matcher mirroring the Node hook's SENSITIVE_MEMORY
# regex: credential/token/secret stems (Hungarian inflections included), card and
# contact data, a bare street-type word, a 12+ digit run, an e-mail, and a phone.
_SENSITIVE = re.compile(
    r"\b(api\s*key|api[-_ ]?kulcs\w*|token|jelsz[oóa]\w*|password|secret|tit[ok]k?\w*|"
    r"bankk[aá]rty\w*|credit\s*card|e-?mail\w*|telefonsz[aá]m\w*|phone)\b"
    r"|\b(?:utca|street|road|avenue)\b"
    r"|\b\d{12,}\b"
    r"|\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b"
    r"|\+?\d[\d\s()-]{7,}\d",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class AdmissionResult:
    """What admission kept, what it rejected and why."""

    outcome: str  # "updated" when anything was accepted, else "skipped"
    accepted: tuple[dict[str, str], ...] = field(default_factory=tuple)
    rejected: tuple[dict[str, str], ...] = field(default_factory=tuple)


def admit(proposal: Proposal, known: frozenset[tuple[str, str]] = frozenset()) -> AdmissionResult:
    """Decide which operations of a proposal may be stored.

    ``known`` is the set of already-stored ``(category, normalized value)`` keys,
    so a repeat is dropped as a duplicate rather than re-applied. Operations past
    ``MAX_OPERATIONS`` are dropped, matching the Node hook's truncation.
    """
    if proposal.kind != "memory_delta":
        return AdmissionResult("skipped")

    operations = proposal.payload["operations"]
    accepted: list[dict[str, str]] = []
    rejected: list[dict[str, str]] = []
    seen = set(known)

    for operation in list(operations)[MAX_OPERATIONS:]:
        rejected.append({**operation, "reason": f"dropped: over operation cap ({MAX_OPERATIONS})"})

    within_cap = list(operations)[:MAX_OPERATIONS]
    # Forgets are applied before upserts, so a supersede within one batch works.
    for operation in sorted(within_cap, key=lambda op: 0 if op["op"] == "forget" else 1):
        value = _collapse(operation["value"])
        reason = _rejection_reason(value)
        key = (operation["category"], value.lower())
        if reason is not None:
            rejected.append({**operation, "reason": reason})
        elif operation["op"] == "upsert" and key in seen:
            rejected.append({**operation, "reason": "duplicate"})
        else:
            accepted.append({"op": operation["op"], "category": operation["category"], "value": value})
            seen.add(key)

    return AdmissionResult(
        "updated" if accepted else "skipped",
        accepted=tuple(accepted),
        rejected=tuple(rejected),
    )


def is_sensitive(value: str) -> bool:
    """Whether a value carries sensitive data and must not be stored."""
    return bool(_SENSITIVE.search(value))


def _rejection_reason(value: str) -> str | None:
    if not value:
        return "empty value"
    if len(value) > MAX_VALUE_LENGTH:
        return f"value over {MAX_VALUE_LENGTH} chars"
    if is_sensitive(value):
        return "contains sensitive data"
    return None


def _collapse(value: str) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""
