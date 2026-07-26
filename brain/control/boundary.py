"""The one place a streamed setpoint can become a command to the vehicle.

`brain/safety/gate.py` guards the mission path: bounded, one-shot commands like
takeoff and goto, each judged on its own. This guards a different shape of risk.
An Offboard stream is continuous, and its danger is not a single bad number but
what a *sequence* does -- a command that arrives too late, a duplicate that
reverses a newer one, or a step change no airframe can follow. Those are
properties of the stream, so the boundary is stateful where the gate is not.

Everything it decides against comes from `twin.yaml`, through `SafetyProfile`.
The boundary holds no limit of its own, so there is nothing here that could
drift looser than the vehicle's contract.

Rejection, never correction. A setpoint over the speed limit is not clamped to
the limit and forwarded: a producer asking for something outside the envelope
has misunderstood the envelope, and quietly giving it a legal version of its
illegal request would hide that for as long as it kept happening.

The boundary cannot arm, cannot change mode, and cannot reach an actuator. It
approves or refuses one velocity, and that is the whole of its authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from math import hypot

from brain.control.contract import (
    OffboardSetpoint,
    SetpointFrame,
    SetpointState,
    Velocity,
)
from brain.safety.profile import OffboardLimits, SafetyProfile


class RejectionReason(Enum):
    """Why a setpoint was refused, in the boundary's own vocabulary.

    Named rather than free text so a scenario can assert the specific failure it
    provoked, and so an audit record says which rule fired rather than how the
    message happened to be worded.
    """

    DISABLED = "offboard_disabled"
    WRONG_VEHICLE = "wrong_vehicle"
    WRONG_FRAME = "wrong_frame"
    PRODUCER_DISTRUSTS_ITS_OWN_SETPOINT = "producer_declared_invalid"
    EXPIRED = "expired"
    TTL_TOO_LONG = "ttl_exceeds_policy"
    STALE_STREAM = "superseded_stream"
    #: The stream fell silent past its timeout and has already fallen back.
    #: Whatever arrives next must start a new stream rather than resume this one.
    STREAM_STOPPED = "stream_stopped"
    OUT_OF_ORDER = "out_of_order"
    RATE_TOO_HIGH = "rate_exceeds_policy"
    SPEED_TOO_HIGH = "speed_exceeds_limit"
    YAW_RATE_TOO_HIGH = "yaw_rate_exceeds_limit"
    ACCELERATION_TOO_HIGH = "acceleration_exceeds_limit"
    TOO_UNCERTAIN = "uncertainty_exceeds_limit"


@dataclass(frozen=True)
class BoundaryDecision:
    """The boundary's verdict on exactly one setpoint."""

    approved: bool
    reason: RejectionReason | None = None
    detail: str = ""
    velocity: Velocity | None = None

    def __post_init__(self) -> None:
        if self.approved and self.reason is not None:
            raise ValueError("An approved decision cannot carry a rejection reason.")
        if not self.approved and self.reason is None:
            raise ValueError("A rejection must name which rule refused it.")
        if not self.approved and self.velocity is not None:
            # A refused setpoint must not hand its velocity onward, however
            # convenient that would be for a caller that forgot to check.
            raise ValueError("A rejected decision must not carry a velocity.")


class OffboardBoundary:
    """Approve only setpoints that are fresh, ordered and inside the envelope."""

    def __init__(self, profile: SafetyProfile) -> None:
        if profile.offboard is None:
            raise ValueError(
                "This twin declares no offboard block, so it has no Offboard control boundary."
            )
        self._profile = profile
        self._limits: OffboardLimits = profile.offboard
        self._expected_frame = SetpointFrame(self._limits.frame)
        self._stream_id: str | None = None
        self._last_sequence: int | None = None
        self._last_accepted_at: datetime | None = None
        self._last_velocity: Velocity | None = None

    @property
    def limits(self) -> OffboardLimits:
        return self._limits

    @property
    def last_accepted_at(self) -> datetime | None:
        """When the boundary last approved a setpoint, for the watchdog to read."""
        return self._last_accepted_at

    def evaluate(self, setpoint: OffboardSetpoint, now: datetime) -> BoundaryDecision:
        """Judge one setpoint. Accepted state advances only on approval."""
        if not self._limits.enabled:
            # The inert default. Nothing below this line runs until a twin and an
            # architecture review together turn the boundary on.
            return _refuse(
                RejectionReason.DISABLED,
                "The Offboard boundary is disabled in twin.yaml.",
            )
        if setpoint.vehicle_id != self._profile.vehicle_id:
            return _refuse(
                RejectionReason.WRONG_VEHICLE,
                f"Setpoint addresses '{setpoint.vehicle_id}', not '{self._profile.vehicle_id}'.",
            )
        if setpoint.frame is not self._expected_frame:
            return _refuse(
                RejectionReason.WRONG_FRAME,
                f"Setpoint is in {setpoint.frame.value}; this twin streams {self._expected_frame.value}.",
            )
        if setpoint.ttl_s > self._limits.max_setpoint_ttl_s:
            return _refuse(
                RejectionReason.TTL_TOO_LONG,
                f"ttl_s {setpoint.ttl_s:g} exceeds the {self._limits.max_setpoint_ttl_s:g} s policy.",
            )

        state = setpoint.state(now)
        if state is SetpointState.INVALID:
            return _refuse(
                RejectionReason.PRODUCER_DISTRUSTS_ITS_OWN_SETPOINT,
                "The producer marked this setpoint invalid, or issued it from the future.",
            )
        if state is SetpointState.EXPIRED:
            return _refuse(
                RejectionReason.EXPIRED,
                f"Setpoint is older than its own {setpoint.ttl_s:g} s ttl.",
            )

        ordering = self._check_ordering(setpoint)
        if ordering is not None:
            return ordering
        rate = self._check_rate(now)
        if rate is not None:
            return rate

        envelope = self._check_envelope(setpoint, now)
        if envelope is not None:
            return envelope

        self._accept(setpoint, now)
        return BoundaryDecision(approved=True, velocity=setpoint.velocity)

    def reset(self) -> None:
        """Forget the stream, so a new one cannot inherit its ordering or timing.

        Called after a fallback: whatever restarts afterwards is a new stream and
        must prove itself from scratch rather than continue where a failed one
        left off.
        """
        self._stream_id = None
        self._last_sequence = None
        self._last_accepted_at = None
        self._last_velocity = None

    def _check_ordering(self, setpoint: OffboardSetpoint) -> BoundaryDecision | None:
        if self._stream_id is None or setpoint.stream_id != self._stream_id:
            if self._stream_id is not None and self._last_accepted_at is not None:
                # A producer may only start a new stream after the old one has
                # gone quiet. Switching mid-flow would let a second producer
                # take over from a live one by picking a different stream_id.
                return _refuse(
                    RejectionReason.STALE_STREAM,
                    f"Stream '{setpoint.stream_id}' arrived while '{self._stream_id}' is still live.",
                )
            return None
        if self._last_sequence is not None and setpoint.sequence <= self._last_sequence:
            return _refuse(
                RejectionReason.OUT_OF_ORDER,
                f"Sequence {setpoint.sequence} does not advance past {self._last_sequence}.",
            )
        return None

    def _check_rate(self, now: datetime) -> BoundaryDecision | None:
        if self._last_accepted_at is None:
            return None
        interval_s = (now - self._last_accepted_at).total_seconds()
        minimum_interval_s = 1.0 / self._limits.max_rate_hz
        if interval_s < minimum_interval_s:
            return _refuse(
                RejectionReason.RATE_TOO_HIGH,
                f"Setpoints {interval_s * 1000:.1f} ms apart exceed {self._limits.max_rate_hz:g} Hz.",
            )
        return None

    def _check_envelope(self, setpoint: OffboardSetpoint, now: datetime) -> BoundaryDecision | None:
        speed_m_s = setpoint.velocity.speed_m_s
        if speed_m_s > self._profile.max_speed_m_s:
            return _refuse(
                RejectionReason.SPEED_TOO_HIGH,
                f"Commanded {speed_m_s:.2f} m/s exceeds the {self._profile.max_speed_m_s:g} m/s limit.",
            )
        yaw_rate = abs(setpoint.velocity.yaw_rate_deg_s)
        if yaw_rate > self._limits.max_yaw_rate_deg_s:
            return _refuse(
                RejectionReason.YAW_RATE_TOO_HIGH,
                f"Commanded {yaw_rate:.1f} deg/s exceeds the {self._limits.max_yaw_rate_deg_s:g} deg/s limit.",
            )
        if (
            setpoint.uncertainty_m_s is not None
            and setpoint.uncertainty_m_s > self._limits.max_uncertainty_m_s
        ):
            return _refuse(
                RejectionReason.TOO_UNCERTAIN,
                f"Producer uncertainty {setpoint.uncertainty_m_s:.2f} m/s exceeds "
                f"the {self._limits.max_uncertainty_m_s:g} m/s limit.",
            )
        return self._check_acceleration(setpoint.velocity, now)

    def _check_acceleration(self, velocity: Velocity, now: datetime) -> BoundaryDecision | None:
        """Bound how sharply the stream may change what it is asking for.

        Both endpoints can sit inside the speed limit while the step between them
        is one no airframe can follow; what PX4 would do with such a command is
        not something to find out in flight.
        """
        if self._last_velocity is None or self._last_accepted_at is None:
            return None
        interval_s = (now - self._last_accepted_at).total_seconds()
        if interval_s <= 0:
            # Guarded by the rate check above; refusing here as well keeps the
            # division safe no matter how this method is reached.
            return _refuse(
                RejectionReason.RATE_TOO_HIGH,
                "Setpoints must be separated by a positive interval.",
            )
        delta = hypot(
            velocity.x_m_s - self._last_velocity.x_m_s,
            velocity.y_m_s - self._last_velocity.y_m_s,
            velocity.z_m_s - self._last_velocity.z_m_s,
        )
        acceleration = delta / interval_s
        if acceleration > self._limits.max_acceleration_m_s2:
            return _refuse(
                RejectionReason.ACCELERATION_TOO_HIGH,
                f"Implied {acceleration:.2f} m/s^2 exceeds "
                f"the {self._limits.max_acceleration_m_s2:g} m/s^2 limit.",
            )
        return None

    def _accept(self, setpoint: OffboardSetpoint, now: datetime) -> None:
        self._stream_id = setpoint.stream_id
        self._last_sequence = setpoint.sequence
        self._last_accepted_at = now
        self._last_velocity = setpoint.velocity


def _refuse(reason: RejectionReason, detail: str) -> BoundaryDecision:
    return BoundaryDecision(approved=False, reason=reason, detail=detail)
