"""Safe, high-level flight mission definitions."""

from dataclasses import dataclass
from math import isfinite

from brain.mission.commands import TakeoffCommand, WaypointCommand
from brain.safety.gate import SafetyGate


class MissionValidationError(ValueError):
    """Raised when a mission contains invalid timing or unsafe commands."""


@dataclass(frozen=True)
class TakeoffHoverLandMission:
    """A bounded mission that leaves all low-level control to PX4."""

    takeoff: TakeoffCommand
    hover_duration_s: float


@dataclass(frozen=True)
class TakeoffWaypointLandMission:
    """A bounded flight that visits one local waypoint before landing."""

    takeoff: TakeoffCommand
    waypoint: WaypointCommand
    hover_duration_s: float
    takeoff_settle_seconds: float = 4.0


def authorize_takeoff_hover_land(
    gate: SafetyGate, target_altitude_m: float, hover_duration_s: float
) -> TakeoffHoverLandMission:
    """Create a mission only after altitude and timing validation succeeds."""
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")

    command = TakeoffCommand(target_altitude_m=target_altitude_m)
    gate.evaluate(command)
    return TakeoffHoverLandMission(takeoff=command, hover_duration_s=hover_duration_s)


def authorize_takeoff_waypoint_land(
    gate: SafetyGate,
    takeoff_altitude_m: float,
    north_m: float,
    east_m: float,
    waypoint_altitude_m: float,
    hover_duration_s: float,
) -> TakeoffWaypointLandMission:
    """Build a single-waypoint mission only after every command is approved."""
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")
    takeoff = TakeoffCommand(target_altitude_m=takeoff_altitude_m)
    waypoint = WaypointCommand(
        north_m=north_m,
        east_m=east_m,
        target_altitude_m=waypoint_altitude_m,
    )
    gate.evaluate(takeoff)
    gate.evaluate(waypoint)
    return TakeoffWaypointLandMission(
        takeoff=takeoff,
        waypoint=waypoint,
        hover_duration_s=hover_duration_s,
    )
