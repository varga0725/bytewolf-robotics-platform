"""Load and validate an Offboard velocity setpoint, failing closed.

Everything else this platform reads is an observation: it describes what is, and
the worst a bad one can do is mislead a decision. A setpoint is different. It is
a command, streamed continuously, and acting on a wrong one moves the vehicle.
So this module refuses anything it cannot fully trust rather than returning a
best-effort value, and it refuses *before* the boundary gets a chance to weigh
limits.

Expiry is resolved here, not declared by the producer: only the consumer knows
the current time. A setpoint older than its own ``ttl_s`` is ``EXPIRED`` and must
not be obeyed -- the situation it was computed for is gone, and a velocity
command that outlives its reason is how a vehicle coasts into something.

The contract carries no arm, mode, actuator or motor field. That is not an
omission to be corrected later: the boundary this feeds exists to be the one
narrow thing a control stream can do, and widening it would defeat its purpose.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
import math
from pathlib import Path
from typing import Any

import jsonschema


OFFBOARD_CONTRACT_VERSION = "v0.1"

#: How far ahead of the consumer's clock a producer may sit before its setpoint
#: is refused. Small skew between machines is normal; a far-future timestamp is
#: not, and treating it as age zero would keep a command obeyable long past the
#: moment it was computed. Deliberately tighter than the observation contract's
#: allowance: a command has a shorter useful life than a measurement.
MAX_CLOCK_SKEW_S = 0.25

OFFBOARD_SETPOINT_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2]
    / "shared/schemas/control/offboard_setpoint_v0_1.schema.json"
)


class OffboardContractError(ValueError):
    """Raised when a document cannot be read as a setpoint at all."""


class SetpointState(Enum):
    """Whether a setpoint may be acted on, and if not, why not."""

    VALID = "valid"
    INVALID = "invalid"
    EXPIRED = "expired"

    @property
    def usable(self) -> bool:
        """Only a trusted, unexpired setpoint may reach the vehicle."""
        return self is SetpointState.VALID


class SetpointFrame(Enum):
    """The frame a velocity is expressed in, stated rather than assumed."""

    LOCAL_NED = "local_ned"
    BODY_FRD = "body_frd"


@dataclass(frozen=True)
class Velocity:
    """A commanded velocity in the setpoint's declared frame.

    ``z_m_s`` is positive downward, following PX4 rather than inverting the sign
    here where it would be easy to lose in translation.
    """

    x_m_s: float
    y_m_s: float
    z_m_s: float
    yaw_rate_deg_s: float

    @property
    def speed_m_s(self) -> float:
        """The magnitude of the commanded translation, ignoring yaw.

        Limits apply to the resulting motion, not to one axis at a time: three
        in-limit components can compose a speed well past the limit.
        """
        return math.sqrt(self.x_m_s**2 + self.y_m_s**2 + self.z_m_s**2)


@dataclass(frozen=True)
class OffboardSetpoint:
    """One validated setpoint and everything needed to judge it."""

    vehicle_id: str
    stream_id: str
    sequence: int
    issued_at: datetime
    ttl_s: float
    frame: SetpointFrame
    declared_validity: str
    velocity: Velocity
    uncertainty_m_s: float | None

    def state(self, now: datetime) -> SetpointState:
        """Resolve obeyability at a given instant, failing closed."""
        if self.declared_validity == "invalid":
            return SetpointState.INVALID
        age_s = (_utc(now) - self.issued_at).total_seconds()
        if age_s < -MAX_CLOCK_SKEW_S:
            # A command from the future is a broken clock, not a fresh command.
            return SetpointState.INVALID
        if age_s > self.ttl_s:
            return SetpointState.EXPIRED
        return SetpointState.VALID


@lru_cache(maxsize=1)
def _validator() -> Any:
    try:
        schema = json.loads(OFFBOARD_SETPOINT_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise OffboardContractError(
            f"Cannot read the setpoint schema '{OFFBOARD_SETPOINT_SCHEMA_PATH}': {error.strerror}."
        ) from error
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def load_setpoint(document: object) -> OffboardSetpoint:
    """Read a document as a setpoint, or refuse it."""
    try:
        _validator().validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise OffboardContractError(f"Setpoint rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."

    velocity_document = document["velocity"]
    velocity = Velocity(
        x_m_s=_finite(velocity_document["x_m_s"], "velocity.x_m_s"),
        y_m_s=_finite(velocity_document["y_m_s"], "velocity.y_m_s"),
        z_m_s=_finite(velocity_document["z_m_s"], "velocity.z_m_s"),
        yaw_rate_deg_s=_finite(velocity_document["yaw_rate_deg_s"], "velocity.yaw_rate_deg_s"),
    )
    uncertainty = document.get("uncertainty_m_s")
    return OffboardSetpoint(
        vehicle_id=document["vehicle_id"],
        stream_id=document["stream_id"],
        sequence=int(document["sequence"]),
        issued_at=_parse_timestamp(document["issued_at"]),
        ttl_s=_finite_ttl(document["ttl_s"]),
        frame=SetpointFrame(document["frame"]),
        declared_validity=document["validity"],
        velocity=velocity,
        uncertainty_m_s=None if uncertainty is None else _finite(uncertainty, "uncertainty_m_s"),
    )


def _finite(value: object, field: str) -> float:
    """Refuse NaN and infinity, which JSON Schema's "number" happily accepts.

    A non-finite velocity is not a large command to be clamped; it is a
    computation that failed, and the platform rejects rather than clamps.
    """
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise OffboardContractError(f"Setpoint field '{field}' must be a finite number.")
    return number


def _finite_ttl(value: object) -> float:
    """Refuse a freshness window that cannot expire.

    JSON has no infinity literal, but an oversized exponent such as ``1e400``
    parses to ``inf`` and satisfies a lower-bound-only schema check. An infinite
    window would make a single setpoint obeyable forever, which is precisely the
    coasting failure the TTL exists to prevent.
    """
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number) or number <= 0:
        raise OffboardContractError("ttl_s must be a positive, finite number of seconds.")
    return number


def _parse_timestamp(value: str) -> datetime:
    if "T" not in value and "t" not in value:
        raise OffboardContractError(
            f"Setpoint timestamp '{value}' is not RFC 3339: date and time must be joined by 'T'."
        )
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise OffboardContractError(f"Setpoint timestamp '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise OffboardContractError(
            f"Setpoint timestamp '{value}' has no offset; an age cannot be measured from it."
        )
    return timestamp.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise OffboardContractError("The current time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
