"""Safety validation that runs before a mission reaches any flight adapter."""

from dataclasses import dataclass
from math import isfinite

from brain.mission.commands import TakeoffCommand


@dataclass(frozen=True)
class FlightLimits:
    max_altitude_m: float
    max_distance_m: float


@dataclass(frozen=True)
class SafetyDecision:
    approved: bool
    command: TakeoffCommand


class SafetyViolation(ValueError):
    """Raised when a command is outside deterministic flight limits."""


class SafetyGate:
    """Approve only high-level commands inside explicit, finite limits."""

    def __init__(self, limits: FlightLimits) -> None:
        self._limits = limits

    def evaluate(self, command: TakeoffCommand) -> SafetyDecision:
        altitude = command.target_altitude_m
        if not isfinite(altitude):
            raise SafetyViolation("Takeoff altitude must be finite.")
        if altitude <= 0.0:
            raise SafetyViolation("Takeoff altitude must be greater than zero.")
        if altitude > self._limits.max_altitude_m:
            raise SafetyViolation(
                f"Takeoff altitude exceeds the {self._limits.max_altitude_m:g} m safety limit."
            )
        return SafetyDecision(approved=True, command=command)
