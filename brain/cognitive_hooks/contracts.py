"""Load and validate the Cognitive Hook Proposal v0.1 contract, failing closed.

A ``Proposal`` is a typed candidate emitted by a background LLM job. This module
only decides whether a document *is* a well-formed proposal; whether its contents
may be kept is the admission pipeline's decision. See
``docs/workstreams/cognitive-hooks.md``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import jsonschema


COGNITIVE_HOOKS_CONTRACT_VERSION = "v0.1"

PROPOSAL_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "shared/schemas/cognitive_hooks/proposal_v0_1.schema.json"
)


class ProposalContractError(ValueError):
    """Raised when a document cannot be read as a proposal."""


@dataclass(frozen=True)
class Proposal:
    """One schema-valid proposal and its provenance."""

    proposal_id: str
    kind: str
    source: dict[str, Any]
    created_at: datetime
    payload: dict[str, Any]


@lru_cache(maxsize=1)
def _validator() -> Any:
    schema = json.loads(PROPOSAL_SCHEMA_PATH.read_text(encoding="utf-8"))
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def load_proposal(document: object) -> Proposal:
    """Read a document as a proposal, or refuse it."""
    try:
        _validator().validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ProposalContractError(f"Proposal rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."
    return Proposal(
        proposal_id=document["proposal_id"],
        kind=document["kind"],
        source=document["source"],
        created_at=_parse_timestamp(document["created_at"]),
        payload=document["payload"],
    )


def _parse_timestamp(value: str) -> datetime:
    if "T" not in value and "t" not in value:
        raise ProposalContractError(
            f"Proposal created_at '{value}' is not RFC 3339: date and time must be joined by 'T'."
        )
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ProposalContractError(f"Proposal created_at '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ProposalContractError(f"Proposal created_at '{value}' has no offset.")
    return timestamp.astimezone(UTC)
