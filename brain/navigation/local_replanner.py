"""Bounded local detour candidates for a shielded Offboard route.

This module proposes only horizontal side-steps.  It neither decides whether a
candidate is safe nor commands a vehicle: the active runtime shield remains the
sole authority that can admit a candidate to the Offboard boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite

from brain.control.contract import Velocity


class ReplanMode(str, Enum):
    DIRECT = "direct"
    RIGHT = "right"
    LEFT = "left"


@dataclass(frozen=True)
class RouteCandidate:
    mode: ReplanMode
    velocity: Velocity


class LocalReplanner:
    """Offer deterministic, sticky lateral candidates after a shield stop.

    The caller must invoke :meth:`select` only after the active shield has
    accepted that candidate.  This prevents an intended detour from becoming a
    second safety authority or from treating an unobserved side as traversable.
    """

    def __init__(
        self, *, lateral_speed_m_s: float, max_detour_s: float = 12.0
    ) -> None:
        if not isfinite(lateral_speed_m_s) or lateral_speed_m_s <= 0.0:
            raise ValueError("lateral_speed_m_s must be positive and finite.")
        if not isfinite(max_detour_s) or max_detour_s <= 0.0:
            raise ValueError("max_detour_s must be positive and finite.")
        self._lateral_speed_m_s = lateral_speed_m_s
        self._max_detour_s = max_detour_s
        self._mode = ReplanMode.DIRECT
        self._ready_at_s: float | None = None
        self._detour_at_s: float | None = None

    @property
    def mode(self) -> ReplanMode:
        return self._mode

    @property
    def lateral_speed_m_s(self) -> float:
        return self._lateral_speed_m_s

    def note_blocked(self, *, now_s: float, stop_duration_s: float) -> None:
        if (
            not isfinite(now_s)
            or not isfinite(stop_duration_s)
            or stop_duration_s < 0.0
        ):
            raise ValueError("Detour timing must be finite and non-negative.")
        if self._ready_at_s is None:
            self._ready_at_s = now_s + stop_duration_s

    def ready(self, *, now_s: float) -> bool:
        if not isfinite(now_s):
            raise ValueError("now_s must be finite.")
        return self._ready_at_s is not None and now_s >= self._ready_at_s

    def exhausted(self, *, now_s: float) -> bool:
        if not isfinite(now_s):
            raise ValueError("now_s must be finite.")
        return (
            self._detour_at_s is not None
            and now_s - self._detour_at_s >= self._max_detour_s
        )

    def candidates(self, direct_velocity: Velocity) -> tuple[RouteCandidate, ...]:
        """Return right/left candidates, prioritising the previously safe side."""
        if direct_velocity.speed_m_s <= 0.0:
            return ()
        right = RouteCandidate(
            ReplanMode.RIGHT,
            Velocity(0.0, self._lateral_speed_m_s, 0.0, 0.0),
        )
        left = RouteCandidate(
            ReplanMode.LEFT,
            Velocity(0.0, -self._lateral_speed_m_s, 0.0, 0.0),
        )
        return (left, right) if self._mode is ReplanMode.LEFT else (right, left)

    def select(self, mode: ReplanMode, *, now_s: float) -> None:
        if mode not in (ReplanMode.RIGHT, ReplanMode.LEFT):
            raise ValueError("Only an accepted lateral detour can be selected.")
        if not isfinite(now_s):
            raise ValueError("now_s must be finite.")
        self._mode = mode
        if self._detour_at_s is None:
            self._detour_at_s = now_s

    def clear(self) -> None:
        self._mode = ReplanMode.DIRECT
        self._ready_at_s = None
        self._detour_at_s = None


__all__ = ["LocalReplanner", "ReplanMode", "RouteCandidate"]
