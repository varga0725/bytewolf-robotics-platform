"""Evidence-backed world memory: what the robot saw, and how far to trust it.

This store is deliberately separate from the personal memory the Pi post-turn
hook writes. Personal memory holds a handful of stable facts the user chose to
share; world memory holds perishable sensor evidence. Mixing them would let a
detection quietly become an identity claim, which is exactly the failure this
module refuses.

Every claim carries its source, observation time, confidence and expiry — all
four, or it is not admitted. Reading is a decision, not a lookup:

* an expired claim is gone, never a slightly older fact;
* a contradicted subject yields *no* fact until the evidence agrees again;
* a claim below the caller's confidence floor never leaves the store.

Nothing here commands. The module has no MAVSDK, PX4, or mission import, and a
claim is evidence a human or planner may read, never an instruction.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
import json
from pathlib import Path
import re
from typing import Any

import jsonschema


WORLD_MEMORY_CONTRACT_VERSION = "v0.1"
WORLD_CLAIM_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared/schemas/world_memory/world_claim_v0_1.schema.json"
)
DEFAULT_MIN_CONFIDENCE = 0.5
MAX_CLAIMS = 500

# A perception pipeline may describe a thing; it may not describe a person.
# Face identification is out of scope, so any wording that reaches for identity
# is refused at admission rather than filtered at read time.
_IDENTITY_CLAIM = re.compile(
    r"\b(arc(felismer|azonos)\w*|face\s*(id|recognition)|biometri\w*|"
    r"szem[eé]lyazonos\w*|identity|szem[eé]ly\w*\s+(neve|azonos\w*))\b",
    re.IGNORECASE,
)


class WorldMemoryError(ValueError):
    """A document cannot be read as an evidence-backed world claim."""


@dataclass(frozen=True)
class WorldClaim:
    """One perishable, sourced claim about the world."""

    subject: str
    category: str
    statement: str
    source: str
    observed_at: datetime
    expires_at: datetime
    confidence: float
    artifact: str | None = None
    vehicle_id: str | None = None
    position: dict[str, Any] | None = None

    @property
    def key(self) -> tuple[str, str]:
        """Two claims describe the same thing when subject and category match."""
        return (self.subject, self.category)

    def is_expired(self, now: datetime) -> bool:
        return _utc(now) >= self.expires_at

    def as_dict(self) -> dict[str, Any]:
        """A read-only projection for the dashboard; evidence always travels with it."""
        document: dict[str, Any] = {
            "subject": self.subject,
            "category": self.category,
            "statement": self.statement,
            "evidence": {
                "source": self.source,
                "observed_at": self.observed_at.isoformat(),
                "expires_at": self.expires_at.isoformat(),
                "confidence": self.confidence,
            },
        }
        if self.vehicle_id is not None:
            document["evidence"]["vehicle_id"] = self.vehicle_id
        if self.artifact is not None:
            document["evidence"]["artifact"] = self.artifact
        if self.position is not None:
            document["position"] = dict(self.position)
        return document


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    try:
        return json.loads(WORLD_CLAIM_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise WorldMemoryError(
            f"Cannot read the world-claim schema '{WORLD_CLAIM_SCHEMA_PATH}': {error.strerror}."
        ) from error


def world_claim_categories() -> frozenset[str]:
    """The categories the contract admits, read from the schema itself.

    Personal memory keeps its own, deliberately disjoint set; deriving this one
    from the schema keeps the two from silently converging.
    """
    return frozenset(_schema()["properties"]["category"]["enum"])


@lru_cache(maxsize=1)
def _validator() -> Any:
    """Compile the contract once instead of once per claim.

    `jsonschema.validate` re-derives and re-checks the schema on every call. The
    schema is the same file every time, so that work is pure repetition — and it
    dominated: reading a log of five thousand claims took eleven seconds, which
    made `/api/v1/world-map` take over a minute and the dashboard's obstacle
    cells simply never arrive after a real survey had run.

    The contract enforced is identical; only the compilation is reused.
    """
    schema = _schema()
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema)


def validate_world_claim_document(document: object) -> None:
    """Check a document against the versioned contract, raising on any breach."""
    try:
        _validator().validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise WorldMemoryError(f"World claim rejected at '{location}': {error.message}") from error


def load_world_claim(document: object) -> WorldClaim:
    """Read a document as a world claim, or refuse it."""
    validate_world_claim_document(document)
    assert isinstance(document, Mapping), "The schema requires an object at the root."
    evidence = document["evidence"]
    observed_at = _timestamp(evidence["observed_at"], "observed_at")
    expires_at = _timestamp(evidence["expires_at"], "expires_at")
    if expires_at <= observed_at:
        raise WorldMemoryError(
            "A world claim must expire after it was observed; otherwise it was never believable."
        )
    statement = str(document["statement"]).strip()
    subject = str(document["subject"]).strip()
    if _IDENTITY_CLAIM.search(statement) or _IDENTITY_CLAIM.search(subject):
        raise WorldMemoryError(
            "World memory does not hold identity or biometric claims; a detection is not a person."
        )
    return WorldClaim(
        subject=subject,
        category=str(document["category"]),
        statement=statement,
        source=str(evidence["source"]),
        observed_at=observed_at,
        expires_at=expires_at,
        confidence=float(evidence["confidence"]),
        artifact=evidence.get("artifact"),
        vehicle_id=evidence.get("vehicle_id"),
        position=dict(document["position"]) if "position" in document else None,
    )


class WorldMemory:
    """An append-only evidence log that resolves what is currently believed.

    Recording never overwrites: a superseded observation stays in the log so a
    contradiction remains visible. Resolution happens at read time, against the
    caller's clock, because only the caller knows what "now" means.
    """

    def __init__(self, claims: Iterable[WorldClaim] = ()) -> None:
        self._claims: list[WorldClaim] = list(claims)[-MAX_CLAIMS:]

    @property
    def claims(self) -> tuple[WorldClaim, ...]:
        """Every recorded claim, newest last, including expired and contradicted ones."""
        return tuple(self._claims)

    def record(self, claim: WorldClaim) -> None:
        self._claims.append(claim)
        del self._claims[:-MAX_CLAIMS]

    def recall(
        self, now: datetime, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE
    ) -> tuple[WorldClaim, ...]:
        """Return only what may be believed right now, one claim per subject."""
        return tuple(
            claim
            for claim, disputed in self._resolve(now, min_confidence).values()
            if not disputed
        )

    def disputed(
        self, now: datetime, *, min_confidence: float = DEFAULT_MIN_CONFIDENCE
    ) -> tuple[WorldClaim, ...]:
        """Subjects whose live evidence disagrees; reported, never silently resolved."""
        return tuple(
            claim
            for claim, disputed in self._resolve(now, min_confidence).values()
            if disputed
        )

    def _resolve(
        self, now: datetime, min_confidence: float
    ) -> dict[tuple[str, str], tuple[WorldClaim, bool]]:
        """Fold the live evidence per subject into a belief and a dispute flag.

        A later observation supersedes an earlier one only when it is at least
        as confident. A later, *less* confident contradiction does not win and
        does not lose either: the subject becomes disputed and yields no fact,
        because guessing between two live measurements is how a stale detection
        turns into a false certainty.
        """
        live = [
            claim
            for claim in self._claims
            if not claim.is_expired(now) and claim.confidence >= min_confidence
        ]
        live.sort(key=lambda claim: claim.observed_at)
        resolved: dict[tuple[str, str], tuple[WorldClaim, bool]] = {}
        for claim in live:
            held = resolved.get(claim.key)
            if held is None:
                resolved[claim.key] = (claim, False)
                continue
            previous, _ = held
            if claim.statement == previous.statement:
                # Agreement refreshes the belief and settles an earlier dispute.
                resolved[claim.key] = (claim, False)
            elif claim.confidence >= previous.confidence:
                resolved[claim.key] = (claim, False)
            else:
                resolved[claim.key] = (previous, True)
        return resolved


def append_claim(path: Path, claim: WorldClaim) -> None:
    """Append one claim to the JSON Lines evidence log."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"contract_version": WORLD_MEMORY_CONTRACT_VERSION, **claim.as_dict()}
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(document, ensure_ascii=False) + "\n")


def load_world_memory(path: Path) -> WorldMemory:
    """Read an evidence log, skipping lines that no longer meet the contract.

    A malformed or superseded-format line is dropped rather than raised on: one
    bad record must not make the whole world unreadable. What survives is always
    a fully evidenced claim.
    """
    claims: list[WorldClaim] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (FileNotFoundError, OSError):
        return WorldMemory()
    for line in lines:
        if not line.strip():
            continue
        try:
            claims.append(load_world_claim(json.loads(line)))
        except (json.JSONDecodeError, WorldMemoryError):
            continue
    return WorldMemory(claims)


def _timestamp(value: str, field: str) -> datetime:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise WorldMemoryError(f"World claim '{field}' is not RFC 3339: '{value}'.") from error
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise WorldMemoryError(f"World claim '{field}' has no offset; it cannot be aged.")
    return moment.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise WorldMemoryError("The current time must be timezone-aware to age a claim.")
    return moment.astimezone(UTC)
