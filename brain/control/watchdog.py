"""Notice when an Offboard stream stops, and decide what happens next.

The boundary judges setpoints that arrive. This judges the ones that do not.
That distinction matters, because the dangerous failure of a continuous control
stream is not a bad command but *silence*: the producer crashes, the link drops,
the planner deadlocks, and the vehicle keeps flying the last thing it was told.

Two clocks, not one, and they fire in a deliberate order:

* a setpoint expires after its own ``ttl_s`` -- the command is no longer valid;
* the stream is declared dead after ``watchdog_timeout_s`` -- the producer is
  gone, not merely late.

The profile loader enforces that the second is longer than the first, so the
vehicle always stops obeying an old command before anyone concludes the stream
died. Between the two the state is ``EXPIRED``: nothing is being commanded, but
the stream may still recover with its next setpoint.

What this module does *not* do is command anything. It returns a decision. A
stopped process cannot execute a fallback either, which is exactly why PX4's own
failsafe remains the authority for that case and is never claimed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from brain.safety.profile import OffboardLimits


class StreamState(Enum):
    """What the watchdog believes about the control stream right now."""

    #: A setpoint was accepted recently enough to still be in force.
    STREAMING = "streaming"
    #: The last accepted setpoint has outlived its ttl, but the stream has not
    #: been silent long enough to be declared dead. Nothing is commanded.
    EXPIRED = "expired"
    #: Silence past the timeout, or an adapter that failed. The stream is gone.
    STOPPED = "stopped"
    #: No setpoint has ever been accepted, so there is nothing to supervise.
    IDLE = "idle"

    @property
    def commands_the_vehicle(self) -> bool:
        return self is StreamState.STREAMING


@dataclass(frozen=True)
class WatchdogDecision:
    """What the watchdog concluded, and the fallback that follows from it."""

    state: StreamState
    #: The ordered fallback the twin declares, empty while nothing is wrong.
    #: The first step is always ``zero_velocity``, enforced by the profile
    #: loader: the vehicle stops asking for motion before anything else is tried.
    fallback: tuple[str, ...] = ()
    reason: str = ""

    @property
    def requires_fallback(self) -> bool:
        return bool(self.fallback)


class OffboardWatchdog:
    """Supervise one control stream against the twin's timing policy."""

    def __init__(self, limits: OffboardLimits) -> None:
        self._limits = limits
        self._last_accepted_at: datetime | None = None
        self._expires_at: datetime | None = None
        self._adapter_failed = False
        self._failure_reason = ""

    def record_accepted(self, accepted_at: datetime, expires_at: datetime) -> None:
        """Note an approved setpoint: when it arrived, and when it stops counting.

        Two instants, because they answer different questions. Silence is
        measured from arrival -- that is when the producer was last heard from.
        Expiry is the contract's own ``issued_at + ttl_s``, passed in rather than
        recomputed from arrival: a setpoint that spent most of its ttl in transit
        must not get a fresh full window on landing, or a command could stay in
        force for nearly twice as long as it was ever valid for.
        """
        self._last_accepted_at = accepted_at
        self._expires_at = expires_at

    def record_adapter_failure(self, reason: str) -> None:
        """Note that the thing carrying setpoints to the vehicle broke.

        Latched deliberately. An adapter that raised once is not trusted again
        because a later call happened not to raise: recovery is an explicit
        ``reset()`` after the fallback has run, not something time heals.
        """
        self._adapter_failed = True
        self._failure_reason = reason or "The Offboard adapter failed."

    def reset(self) -> None:
        """Forget this stream entirely, after a fallback or a deliberate stop."""
        self._last_accepted_at = None
        self._expires_at = None
        self._adapter_failed = False
        self._failure_reason = ""

    def evaluate(self, now: datetime) -> WatchdogDecision:
        """Decide the stream's state at this instant, failing closed."""
        if self._adapter_failed:
            return WatchdogDecision(
                state=StreamState.STOPPED,
                fallback=self._limits.fallback_sequence,
                reason=self._failure_reason,
            )
        if self._last_accepted_at is None or self._expires_at is None:
            # Never commanded, so there is nothing to fall back from. Starting a
            # fallback here would land a vehicle that is sitting on the ground.
            return WatchdogDecision(state=StreamState.IDLE)

        silence_s = (now - self._last_accepted_at).total_seconds()
        if silence_s < 0:
            # The clock moved backwards. Treating this as freshness would extend
            # the life of a command that should already have expired.
            return WatchdogDecision(
                state=StreamState.STOPPED,
                fallback=self._limits.fallback_sequence,
                reason="The clock moved backwards; stream timing cannot be trusted.",
            )
        if silence_s > self._limits.watchdog_timeout_s:
            return WatchdogDecision(
                state=StreamState.STOPPED,
                fallback=self._limits.fallback_sequence,
                reason=(
                    f"No accepted setpoint for {silence_s:.2f} s, past the "
                    f"{self._limits.watchdog_timeout_s:g} s timeout."
                ),
            )
        if now > self._expires_at:
            overdue_s = (now - self._expires_at).total_seconds()
            return WatchdogDecision(
                state=StreamState.EXPIRED,
                reason=(
                    f"The last setpoint expired {overdue_s:.2f} s ago; nothing is commanded."
                ),
            )
        return WatchdogDecision(state=StreamState.STREAMING)

    def deadline(self) -> datetime | None:
        """When the stream will be declared dead if nothing else is accepted."""
        if self._last_accepted_at is None:
            return None
        return self._last_accepted_at + timedelta(seconds=self._limits.watchdog_timeout_s)
