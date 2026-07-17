"""Validate observations and resolve whether they may be acted on.

An observation describes what is; it never commands. This module is the only
place that decides an observation is usable, and it fails closed: anything a
consumer cannot fully trust resolves to a state that forbids acting on it.

The four states a consumer must tell apart are deliberately distinct:

* ``VALID``    - the producer trusts it and it is still fresh.
* ``INVALID``  - the producer measured but does not trust the result.
* ``MISSING``  - the producer has no measurement, so there is no payload.
* ``STALE``    - trustworthy when taken, but older than its own max_age_s.

Only the consumer knows the current time, so staleness is derived here rather
than declared by the producer. Age is measured from ``observed_at`` -- when the
measurement was taken -- so a slow pipeline cannot hide behind a fresh publish
time.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import jsonschema


OBSERVATION_CONTRACT_VERSION = "v0.1"
OBSERVATION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared/schemas/observation/observation_v0_1.schema.json"
)


class ObservationContractError(ValueError):
    """Raised when a document cannot be read as an observation at all."""


class ObservationState(Enum):
    """Whether an observation may be acted on, and if not, why not."""

    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    STALE = "stale"

    @property
    def usable(self) -> bool:
        """Only a valid, fresh observation may drive a decision."""
        return self is ObservationState.VALID


@dataclass(frozen=True)
class Observation:
    """One schema-valid observation and the payload it carries, if any."""

    kind: str
    vehicle_id: str
    observed_at: datetime
    max_age_s: float
    declared_validity: str
    payload: dict[str, Any] | None
    source: str | None

    def age_s(self, now: datetime) -> float:
        """Seconds since the measurement was taken, never negative."""
        return max(0.0, (_utc(now) - self.observed_at).total_seconds())

    def state(self, now: datetime) -> ObservationState:
        """Resolve the four-way state at a given instant, failing closed."""
        if self.declared_validity == "missing":
            return ObservationState.MISSING
        if self.declared_validity == "invalid":
            return ObservationState.INVALID
        if self.age_s(now) > self.max_age_s:
            return ObservationState.STALE
        return ObservationState.VALID

    def usable_payload(self, now: datetime) -> dict[str, Any]:
        """Return the payload only if it may be acted on, and refuse otherwise."""
        state = self.state(now)
        if not state.usable:
            raise ObservationContractError(
                f"Observation '{self.kind}' is {state.value} and must not be acted on."
            )
        assert self.payload is not None, "A valid observation always carries a payload."
        return self.payload


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    try:
        return json.loads(OBSERVATION_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise ObservationContractError(
            f"Cannot read the observation schema '{OBSERVATION_SCHEMA_PATH}': {error.strerror}."
        ) from error


def validate_observation_document(document: object) -> None:
    """Check a document against the versioned contract, raising on any breach."""
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise ObservationContractError(f"Observation rejected at '{location}': {error.message}") from error


def load_observation(document: object) -> Observation:
    """Read a document as an observation, or refuse it."""
    validate_observation_document(document)
    assert isinstance(document, dict), "The schema requires an object at the root."
    return Observation(
        kind=document["kind"],
        vehicle_id=document["vehicle_id"],
        observed_at=_parse_timestamp(document["observed_at"]),
        max_age_s=float(document["max_age_s"]),
        declared_validity=document["validity"],
        payload=document.get("payload"),
        source=document.get("source"),
    )


def _parse_timestamp(value: str) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ObservationContractError(f"Observation timestamp '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ObservationContractError(
            f"Observation timestamp '{value}' has no offset; an age cannot be measured from it."
        )
    return timestamp.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ObservationContractError("The current time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
