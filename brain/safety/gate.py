"""Safety validation that runs before a mission reaches any flight adapter."""

from dataclasses import dataclass
from math import fabs, hypot, isfinite

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand


@dataclass(frozen=True)
class LocalPolygonGeofence:
    """A closed, launch-relative allowed area in local north/east metres.

    The fence is intentionally evaluated before a command reaches MAVSDK.  It
    is therefore suitable for deterministic SITL evidence and remains a
    second, independent guard in front of any PX4 geofence configuration.
    """

    vertices_m: tuple[tuple[float, float], ...]

    def __post_init__(self) -> None:
        normalized = tuple((float(north_m), float(east_m)) for north_m, east_m in self.vertices_m)
        if len(normalized) < 3:
            raise ValueError("A geofence must contain at least three vertices.")
        if not all(isfinite(value) for vertex in normalized for value in vertex):
            raise ValueError("Geofence vertices must be finite local coordinates.")
        if len(set(normalized)) < 3:
            raise ValueError("A geofence must contain at least three distinct vertices.")
        area_twice = sum(
            north_m * next_east_m - east_m * next_north_m
            for (north_m, east_m), (next_north_m, next_east_m) in zip(
                normalized, normalized[1:] + normalized[:1]
            )
        )
        if fabs(area_twice) <= 1e-9:
            raise ValueError("A geofence must enclose a non-zero area.")
        object.__setattr__(self, "vertices_m", normalized)

    def contains(self, north_m: float, east_m: float) -> bool:
        """Return whether a point lies inside the polygon, including its boundary."""
        if not isfinite(north_m) or not isfinite(east_m):
            return False
        point = (north_m, east_m)
        vertices = self.vertices_m
        if any(_point_is_on_segment(point, start, end) for start, end in zip(vertices, vertices[1:] + vertices[:1])):
            return True

        inside = False
        previous_north_m, previous_east_m = vertices[-1]
        for current_north_m, current_east_m in vertices:
            crosses_east = (current_east_m > east_m) != (previous_east_m > east_m)
            if crosses_east:
                intersection_north_m = (
                    (previous_north_m - current_north_m)
                    * (east_m - current_east_m)
                    / (previous_east_m - current_east_m)
                    + current_north_m
                )
                if north_m < intersection_north_m:
                    inside = not inside
            previous_north_m, previous_east_m = current_north_m, current_east_m
        return inside


@dataclass(frozen=True)
class FlightLimits:
    max_altitude_m: float
    max_distance_m: float
    allowed_geofence: LocalPolygonGeofence | None = None


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
        if self._limits.allowed_geofence and not self._limits.allowed_geofence.contains(
            command.north_m, command.east_m
        ):
            raise SafetyViolation("Waypoint is outside the allowed geofence.")
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


def _point_is_on_segment(
    point: tuple[float, float], start: tuple[float, float], end: tuple[float, float]
) -> bool:
    """Use a small fixed tolerance so configured fence boundaries are allowed."""
    north_m, east_m = point
    start_north_m, start_east_m = start
    end_north_m, end_east_m = end
    cross = (north_m - start_north_m) * (end_east_m - start_east_m) - (
        east_m - start_east_m
    ) * (end_north_m - start_north_m)
    if fabs(cross) > 1e-9:
        return False
    return (
        min(start_north_m, end_north_m) - 1e-9 <= north_m <= max(start_north_m, end_north_m) + 1e-9
        and min(start_east_m, end_east_m) - 1e-9 <= east_m <= max(start_east_m, end_east_m) + 1e-9
    )
