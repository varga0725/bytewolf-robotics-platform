"""Safety validation that runs before a mission reaches any flight adapter."""

from dataclasses import dataclass
from math import hypot, isfinite

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand


@dataclass(frozen=True)
class FlightLimits:
    max_altitude_m: float
    max_distance_m: float


@dataclass(frozen=True)
class SafetyDecision:
    approved: bool
    command: TakeoffCommand | WaypointCommand | ReturnToHomeCommand | LandCommand


class SafetyViolation(ValueError):
    """Raised when a command is outside deterministic flight limits."""


class SafetyGate:
    """Approve only high-level commands inside explicit, finite limits."""

    def __init__(self, limits: FlightLimits) -> None:
        self._limits = limits

    def evaluate(
        self, command: TakeoffCommand | WaypointCommand | ReturnToHomeCommand | LandCommand
    ) -> SafetyDecision:
        if isinstance(command, WaypointCommand):
            self._validate_waypoint(command)
            return SafetyDecision(approved=True, command=command)
        if isinstance(command, ReturnToHomeCommand):
            self._validate_altitude(command.target_altitude_m, "Return-to-Home")
            return SafetyDecision(approved=True, command=command)
        if isinstance(command, LandCommand):
            return SafetyDecision(approved=True, command=command)
        self._validate_altitude(command.target_altitude_m, "Takeoff")
        return SafetyDecision(approved=True, command=command)

    def _validate_waypoint(self, command: WaypointCommand) -> None:
        values = (command.north_m, command.east_m, command.target_altitude_m)
        if not all(isfinite(value) for value in values):
            raise SafetyViolation("Waypoint coordinates and altitude must be finite.")
        distance = hypot(command.north_m, command.east_m)
        if distance > self._limits.max_distance_m:
            raise SafetyViolation(
                f"Waypoint exceeds the {self._limits.max_distance_m:g} m safety distance limit."
            )
        self._validate_altitude(command.target_altitude_m, "Waypoint")

    def _validate_altitude(self, altitude: float, command_name: str) -> None:
        if not isfinite(altitude):
            raise SafetyViolation(f"{command_name} altitude must be finite.")
        if altitude <= 0.0:
            raise SafetyViolation(f"{command_name} altitude must be greater than zero.")
        if altitude > self._limits.max_altitude_m:
            raise SafetyViolation(
                f"{command_name} altitude exceeds the {self._limits.max_altitude_m:g} m safety limit."
            )
