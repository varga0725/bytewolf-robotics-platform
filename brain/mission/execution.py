"""Immutable mission execution state and audit events."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class MissionPhase(StrEnum):
    """Observable phases for the bounded flight mission."""

    ARMING = "arming"
    TAKING_OFF = "taking_off"
    NAVIGATING = "navigating"
    HOVERING = "hovering"
    HOLDING = "holding"
    RETURNING = "returning"
    LANDING = "landing"
    COMPLETED = "completed"
    FAILED = "failed"


class MissionTransitionError(ValueError):
    """Raised when an execution skips a safety-relevant phase."""


@dataclass(frozen=True)
class MissionEvent:
    phase: MissionPhase
    timestamp: datetime


@dataclass(frozen=True)
class MissionExecution:
    """An immutable, ordered audit trail for a single mission run."""

    events: tuple[MissionEvent, ...]

    @classmethod
    def empty(cls) -> "MissionExecution":
        return cls(events=())

    @property
    def phase(self) -> MissionPhase | None:
        return self.events[-1].phase if self.events else None

    def transition(
        self, phase: MissionPhase, timestamp: datetime | None = None
    ) -> "MissionExecution":
        if phase not in _ALLOWED_TRANSITIONS[self.phase]:
            previous = self.phase.value if self.phase else "initial"
            raise MissionTransitionError(f"Cannot transition from {previous} to {phase.value}.")
        event = MissionEvent(phase=phase, timestamp=timestamp or datetime.now(UTC))
        return MissionExecution(events=(*self.events, event))


# FAILED terminates any phase that has begun: a mission can fail before it is
# airborne, and recording that is never a claim that a landing was commanded.
_ALLOWED_TRANSITIONS: dict[MissionPhase | None, frozenset[MissionPhase]] = {
    None: frozenset({MissionPhase.ARMING}),
    MissionPhase.ARMING: frozenset(
        {MissionPhase.TAKING_OFF, MissionPhase.LANDING, MissionPhase.FAILED}
    ),
    MissionPhase.TAKING_OFF: frozenset(
        {MissionPhase.NAVIGATING, MissionPhase.HOVERING, MissionPhase.LANDING, MissionPhase.FAILED}
    ),
    MissionPhase.NAVIGATING: frozenset(
        {MissionPhase.HOVERING, MissionPhase.LANDING, MissionPhase.FAILED}
    ),
    MissionPhase.HOVERING: frozenset(
        {
            MissionPhase.HOLDING,
            MissionPhase.RETURNING,
            MissionPhase.LANDING,
            MissionPhase.FAILED,
        }
    ),
    MissionPhase.HOLDING: frozenset({MissionPhase.LANDING, MissionPhase.FAILED}),
    MissionPhase.RETURNING: frozenset(
        {MissionPhase.LANDING, MissionPhase.COMPLETED, MissionPhase.FAILED}
    ),
    MissionPhase.LANDING: frozenset({MissionPhase.COMPLETED, MissionPhase.FAILED}),
    MissionPhase.COMPLETED: frozenset(),
    MissionPhase.FAILED: frozenset(),
}
