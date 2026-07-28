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
    ADVANCE = "advance"


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
        self, *, lateral_speed_m_s: float, max_detour_s: float = 12.0,
        required_bypass_offset_m: float = 3.0,
    ) -> None:
        if not isfinite(lateral_speed_m_s) or lateral_speed_m_s <= 0.0:
            raise ValueError("lateral_speed_m_s must be positive and finite.")
        if not isfinite(max_detour_s) or max_detour_s <= 0.0:
            raise ValueError("max_detour_s must be positive and finite.")
        if not isfinite(required_bypass_offset_m) or required_bypass_offset_m <= 0.0:
            raise ValueError("required_bypass_offset_m must be positive and finite.")
        self._lateral_speed_m_s = lateral_speed_m_s
        self._max_detour_s = max_detour_s
        self._required_bypass_offset_m = required_bypass_offset_m
        self._mode = ReplanMode.DIRECT
        self._ready_at_s: float | None = None
        self._detour_at_s: float | None = None
        self._detour_cross_track_error_m: float | None = None

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

    def may_resume_direct(self, *, cross_track_error_m: float) -> bool:
        if not isfinite(cross_track_error_m):
            raise ValueError("cross_track_error_m must be finite.")
        return self._detour_cross_track_error_m is not None and abs(
            cross_track_error_m - self._detour_cross_track_error_m
        ) >= self._required_bypass_offset_m

    def advance(self) -> None:
        if self._mode not in (ReplanMode.RIGHT, ReplanMode.LEFT):
            raise ValueError("Advance requires an accepted lateral detour.")
        self._mode = ReplanMode.ADVANCE

    def candidates(
        self, *, right_velocity: Velocity, left_velocity: Velocity
    ) -> tuple[RouteCandidate, ...]:
        """Return route-relative candidates, prioritising the safe side.

        The caller rotates the route's world-frame cross-track vectors into
        ``body_frd`` using the current heading.  This class retains only the
        bounded selection and progress state.
        """
        if right_velocity.speed_m_s <= 0.0 or left_velocity.speed_m_s <= 0.0:
            return ()
        right = RouteCandidate(ReplanMode.RIGHT, right_velocity)
        left = RouteCandidate(ReplanMode.LEFT, left_velocity)
        return (left, right) if self._mode is ReplanMode.LEFT else (right, left)

    def select(
        self, mode: ReplanMode, *, now_s: float, cross_track_error_m: float = 0.0
    ) -> None:
        if mode not in (ReplanMode.RIGHT, ReplanMode.LEFT):
            raise ValueError("Only an accepted lateral detour can be selected.")
        if not isfinite(now_s):
            raise ValueError("now_s must be finite.")
        if not isfinite(cross_track_error_m):
            raise ValueError("cross_track_error_m must be finite.")
        self._mode = mode
        if self._detour_at_s is None:
            self._detour_at_s = now_s
            self._detour_cross_track_error_m = cross_track_error_m

    def clear(self) -> None:
        self._mode = ReplanMode.DIRECT
        self._ready_at_s = None
        self._detour_at_s = None
        self._detour_cross_track_error_m = None


__all__ = ["LocalReplanner", "ReplanMode", "RouteCandidate"]
