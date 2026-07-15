"""Mission commands are immutable requests, never direct motor controls."""

from dataclasses import dataclass


@dataclass(frozen=True)
class TakeoffCommand:
    """Request a controlled PX4 takeoff to a relative altitude in metres."""

    target_altitude_m: float
