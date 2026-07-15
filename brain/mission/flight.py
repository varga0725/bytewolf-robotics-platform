"""Safe, high-level flight mission definitions."""

from dataclasses import dataclass, field
from math import isfinite

from brain.mission.commands import ReturnToHomeCommand, TakeoffCommand, WaypointCommand
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
    waypoint_tolerance_m: float = 1.0
    waypoint_timeout_s: float = 30.0


@dataclass(frozen=True)
class TakeoffReturnToHomeMission:
    """Take off, briefly hover, then ask PX4 to return to launch and land."""

    takeoff: TakeoffCommand
    hover_duration_s: float
    return_to_home: ReturnToHomeCommand = field(
        default_factory=lambda: ReturnToHomeCommand(target_altitude_m=2.0)
    )
    takeoff_settle_seconds: float = 4.0
    landing_timeout_s: float = 60.0


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
    waypoint_tolerance_m: float = 1.0,
    waypoint_timeout_s: float = 30.0,
) -> TakeoffWaypointLandMission:
    """Build a single-waypoint mission only after every command is approved."""
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")
    if not isfinite(waypoint_tolerance_m) or waypoint_tolerance_m <= 0.0:
        raise MissionValidationError("Waypoint tolerance must be a positive, finite number of metres.")
    if not isfinite(waypoint_timeout_s) or waypoint_timeout_s <= 0.0:
        raise MissionValidationError("Waypoint timeout must be a positive, finite number of seconds.")
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
        waypoint_tolerance_m=waypoint_tolerance_m,
        waypoint_timeout_s=waypoint_timeout_s,
    )


def authorize_takeoff_return_to_home(
    gate: SafetyGate,
    takeoff_altitude_m: float,
    hover_duration_s: float,
    landing_timeout_s: float = 60.0,
) -> TakeoffReturnToHomeMission:
    """Build a return-to-home mission after timing and safety validation."""
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")
    if not isfinite(landing_timeout_s) or landing_timeout_s <= 0.0:
        raise MissionValidationError("Landing timeout must be a positive, finite number of seconds.")
    takeoff = TakeoffCommand(target_altitude_m=takeoff_altitude_m)
    return_to_home = ReturnToHomeCommand(target_altitude_m=takeoff_altitude_m)
    gate.evaluate(takeoff)
    gate.evaluate(return_to_home)
    return TakeoffReturnToHomeMission(
        takeoff=takeoff,
        return_to_home=return_to_home,
        hover_duration_s=hover_duration_s,
        landing_timeout_s=landing_timeout_s,
    )
