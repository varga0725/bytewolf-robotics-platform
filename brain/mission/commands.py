"""Mission commands are immutable requests, never direct motor controls."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TakeoffCommand:
    """Request a controlled PX4 takeoff to a relative altitude in metres."""

    target_altitude_m: float


@dataclass(frozen=True)
class WaypointCommand:
    """Request a relative north/east movement at a bounded target altitude."""

    north_m: float
    east_m: float
    target_altitude_m: float


@dataclass(frozen=True)
class ReturnToHomeCommand:
    """Request PX4 to return to the recorded launch position and land."""

    target_altitude_m: float
