"""One Offboard control session: boundary, watchdog and the adapter seam.

This is the composition point. The boundary judges each setpoint, the watchdog
judges the silence between them, and this decides what actually reaches the
vehicle -- which, in shadow mode, is nothing at all.

Shadow mode is the default and the reason this module can exist before any
architecture review. Every setpoint is validated, every rejection recorded,
every fallback decided, and the adapter is simply never called. That produces
the evidence the roadmap asks for (intervention rate, rejection reasons, timing)
without a single command leaving the process. Going active is a twin change plus
an explicit constructor argument, not a default anyone can drift into.

The adapter protocol has exactly one method. It cannot arm, cannot change mode,
cannot touch an actuator, and there is a static test asserting that no wider
MAVSDK surface is reachable from this package. The mission path keeps its own
adapter; this one is not a second way to fly a mission, it is a way to stream a
velocity that the boundary has already approved.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Protocol

from brain.control.boundary import BoundaryDecision, OffboardBoundary, RejectionReason
from brain.control.contract import OffboardSetpoint, Velocity
from brain.control.watchdog import OffboardWatchdog, StreamState, WatchdogDecision
from brain.safety.profile import SafetyProfile


class OffboardAdapterError(RuntimeError):
    """Raised by an adapter that could not deliver a setpoint."""


class VelocityAdapter(Protocol):
    """The entire surface a control stream is allowed to reach.

    One method, one direction, no return channel. Anything wider -- arming, mode
    changes, actuator access -- belongs to PX4 and to the mission path, and is
    deliberately unreachable from here.
    """

    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None:
        """Deliver one approved velocity, or raise ``OffboardAdapterError``."""
        ...


@dataclass(frozen=True)
class SessionOutcome:
    """What happened to one offered setpoint, end to end."""

    decision: BoundaryDecision
    watchdog: WatchdogDecision
    #: True only when the velocity actually left the process. Always false in
    #: shadow mode, and false on every outcome ``offer`` returns, so a reader
    #: can never mistake a validated setpoint for a delivered one.
    delivered: bool = False
    #: The frame the approved velocity is expressed in, carried so ``deliver``
    #: needs nothing but the outcome.
    frame: str | None = None

    @property
    def approved(self) -> bool:
        return self.decision.approved


@dataclass
class SessionRecord:
    """A replayable account of the session, for the audit artifact.

    Counting rejections by reason is the point: an intervention rate means
    nothing without knowing which rule intervened, and a stream that is refused
    a thousand times for one reason is a different problem from one refused once
    each for a thousand.
    """

    offered: int = 0
    approved: int = 0
    delivered: int = 0
    rejections: dict[str, int] = field(default_factory=dict)
    fallbacks: list[str] = field(default_factory=list)

    def note_rejection(self, reason: RejectionReason) -> None:
        self.rejections[reason.value] = self.rejections.get(reason.value, 0) + 1

    def as_document(self) -> dict[str, object]:
        """The shape that goes into a mission artifact."""
        return {
            "offered": self.offered,
            "approved": self.approved,
            "delivered": self.delivered,
            "rejections": dict(sorted(self.rejections.items())),
            "fallbacks": list(self.fallbacks),
        }


class OffboardSession:
    """Validate a control stream and, only when active, deliver it."""

    def __init__(
        self,
        profile: SafetyProfile,
        adapter: VelocityAdapter | None = None,
        *,
        shadow: bool = True,
    ) -> None:
        if profile.offboard is None:
            raise ValueError("This twin declares no offboard block; there is no session to run.")
        if not shadow and adapter is None:
            raise ValueError("An active session needs an adapter to deliver setpoints to.")
        if not shadow and not profile.offboard.enabled:
            # Two independent switches must agree. The twin is the vehicle's own
            # policy; the constructor argument is this run's intent. Either one
            # alone leaving the boundary live would be a single point of failure.
            raise ValueError(
                "The twin disables Offboard control, so an active session cannot be started."
            )
        self._boundary = OffboardBoundary(profile)
        self._watchdog = OffboardWatchdog(profile.offboard)
        self._adapter = adapter
        self._shadow = shadow
        self.record = SessionRecord()

    @property
    def shadow(self) -> bool:
        return self._shadow

    def offer(self, setpoint: OffboardSetpoint, now: datetime) -> SessionOutcome:
        """Judge one setpoint. Delivery, if any, is ``deliver``'s job."""
        self.record.offered += 1

        # Ask the watchdog first. A caller that stopped polling -- a blocked
        # event loop, a slow iteration -- could otherwise hand over a fresh
        # setpoint after the stream had already been silent past its timeout,
        # and accepting it would overwrite the evidence of the gap before anyone
        # looked. The stream would resume, in active mode, without ever
        # producing the fallback its own silence had earned.
        pending = self._watchdog.evaluate(now)
        if pending.requires_fallback:
            self._note_fallback(pending)
            decision = BoundaryDecision(
                approved=False,
                reason=RejectionReason.STREAM_STOPPED,
                detail=pending.reason,
            )
            self.record.note_rejection(RejectionReason.STREAM_STOPPED)
            return SessionOutcome(decision=decision, watchdog=pending)

        decision = self._boundary.evaluate(setpoint, now)
        if not decision.approved:
            assert decision.reason is not None, "The boundary guarantees a reason."
            self.record.note_rejection(decision.reason)
            # A rejected setpoint does not restart the watchdog clock. Otherwise
            # a producer flooding the boundary with refused commands would keep
            # the stream looking alive while commanding nothing.
            return SessionOutcome(decision=decision, watchdog=self._watchdog.evaluate(now))

        self.record.approved += 1
        # Expiry is the contract's own instant, not "ttl_s from arrival": a
        # setpoint that spent most of its window in transit keeps the deadline
        # it was issued with.
        self._watchdog.record_accepted(now, setpoint.issued_at + timedelta(seconds=setpoint.ttl_s))
        return SessionOutcome(
            decision=decision,
            watchdog=self._watchdog.evaluate(now),
            frame=setpoint.frame.value,
        )

    async def deliver(self, outcome: SessionOutcome, now: datetime) -> SessionOutcome:
        """Send an approved velocity to the vehicle, if this session may.

        Separate from ``offer`` because the two are different kinds of thing.
        Deciding is pure, synchronous and deterministic -- which is what lets
        the whole envelope be tested without an event loop. Delivering is I/O
        against an async flight stack. Folding the second into the first would
        have made every shadow-mode caller await something that, by definition,
        never happens.

        In shadow mode this returns the outcome untouched: the point of shadow
        is that a fully exercised session delivers nothing.
        """
        if not outcome.approved or self._shadow:
            return outcome
        assert self._adapter is not None, "An active session was constructed with an adapter."
        assert outcome.decision.velocity is not None, "An approved decision carries its velocity."
        assert outcome.frame is not None, "An approved outcome carries its frame."
        try:
            await self._adapter.send_velocity_async(outcome.decision.velocity, outcome.frame)
        except OffboardAdapterError as error:
            self._watchdog.record_adapter_failure(str(error))
            watchdog = self._watchdog.evaluate(now)
            self._note_fallback(watchdog)
            return replace(outcome, watchdog=watchdog, delivered=False)
        self.record.delivered += 1
        return replace(outcome, delivered=True)

    def poll(self, now: datetime) -> WatchdogDecision:
        """Ask the watchdog about the silence, with no setpoint on offer.

        This is what a control loop calls between setpoints. It is the only way
        a stopped stream is ever noticed, because a producer that has died sends
        nothing to notice it by.
        """
        watchdog = self._watchdog.evaluate(now)
        if watchdog.requires_fallback:
            self._note_fallback(watchdog)
        return watchdog

    def _note_fallback(self, watchdog: WatchdogDecision) -> None:
        """Record one fallback and clear the session it ended.

        Recorded unconditionally, and deduplicated by the reset rather than by
        comparing against the last entry. Every permitted sequence begins with
        ``zero_velocity``, so comparing steps suppressed a genuine *second*
        failure just as readily as a repeated poll, and the artifact undercounted
        exactly the events it exists to count.

        Resetting both halves is what stops a control loop polling at 20 Hz from
        writing the same event forever: after this the watchdog supervises
        nothing, and whatever restarts must prove itself as a new stream.
        """
        if not watchdog.requires_fallback:
            return
        self.record.fallbacks.append(watchdog.fallback[0])
        self._boundary.reset()
        self._watchdog.reset()

    def stop(self) -> None:
        """End the session deliberately, so nothing outlives it."""
        self._boundary.reset()
        self._watchdog.reset()


__all__ = [
    "OffboardAdapterError",
    "OffboardSession",
    "SessionOutcome",
    "SessionRecord",
    "StreamState",
    "VelocityAdapter",
]
