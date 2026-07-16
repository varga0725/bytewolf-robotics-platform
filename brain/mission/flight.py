"""Safe, high-level flight mission definitions."""

from dataclasses import dataclass, field
from enum import StrEnum
from math import isfinite

from brain.mission.commands import ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.safety.gate import SafetyGate


class MissionValidationError(ValueError):
    """Raised when a mission contains invalid timing or unsafe commands."""


class InterruptionAction(StrEnum):
    """The two explicit flight-interruption actions exercised by P0.v2."""

    HOLD = "hold"
    LAND = "land"


@dataclass(frozen=True)
class TakeoffHoverLandMission:
    """A bounded mission that leaves all low-level control to PX4."""

    takeoff: TakeoffCommand
    hover_duration_s: float


@dataclass(frozen=True)
class TakeoffInterruptLandMission:
    """Take off, deliberately interrupt, then guarantee a terminal landing.

    A HOLD interrupt is intentionally followed by one cleanup LAND command so
    that no P0 scenario leaves a vehicle airborne after its evidence is saved.
    """

    takeoff: TakeoffCommand
    interrupt_after_s: float
    interruption_action: InterruptionAction
    hold_cleanup_s: float = 1.0


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
class TakeoffWaypointSquareLandMission:
    """A bounded four-leg local square, with a confirmed arrival at every corner.

    Each waypoint is a displacement from the vehicle's current position. This
    matches MAVSDK's local waypoint conversion and keeps every leg's reference
    frame explicit.
    """

    takeoff: TakeoffCommand
    waypoints: tuple[WaypointCommand, WaypointCommand, WaypointCommand, WaypointCommand]
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
    home_tolerance_m: float = 6.0


def authorize_takeoff_hover_land(
    gate: SafetyGate, target_altitude_m: float, hover_duration_s: float
) -> TakeoffHoverLandMission:
    """Create a mission only after altitude and timing validation succeeds."""
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")

    command = TakeoffCommand(target_altitude_m=target_altitude_m)
    gate.evaluate(command)
    return TakeoffHoverLandMission(takeoff=command, hover_duration_s=hover_duration_s)


def authorize_takeoff_interrupt_land(
    gate: SafetyGate,
    takeoff_altitude_m: float,
    interrupt_after_s: float,
    interruption_action: InterruptionAction,
    hold_cleanup_s: float = 1.0,
) -> TakeoffInterruptLandMission:
    """Authorize a controlled interruption that always terminates on land."""
    if not isfinite(interrupt_after_s) or interrupt_after_s <= 0.0:
        raise MissionValidationError("Interruption delay must be a positive, finite number of seconds.")
    if not isfinite(hold_cleanup_s) or hold_cleanup_s < 0.0:
        raise MissionValidationError("Hold cleanup duration must be a finite non-negative number of seconds.")
    if not isinstance(interruption_action, InterruptionAction):
        raise MissionValidationError("Interruption action must be HOLD or LAND.")
    takeoff = TakeoffCommand(target_altitude_m=takeoff_altitude_m)
    gate.evaluate(takeoff)
    return TakeoffInterruptLandMission(
        takeoff=takeoff,
        interrupt_after_s=interrupt_after_s,
        interruption_action=interruption_action,
        hold_cleanup_s=hold_cleanup_s,
    )


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


def authorize_takeoff_waypoint_square_land(
    gate: SafetyGate,
    takeoff_altitude_m: float,
    side_length_m: float,
    waypoint_altitude_m: float,
    hover_duration_s: float,
    waypoint_tolerance_m: float = 1.0,
    waypoint_timeout_s: float = 30.0,
) -> TakeoffWaypointSquareLandMission:
    """Authorize all square corners before any flight operation can begin.

    The final corner is the launch-relative origin, so the complete square is
    observable in the simulator while remaining inside the same local safety
    boundary as every other waypoint mission.
    """
    if not isfinite(side_length_m) or side_length_m <= 0.0:
        raise MissionValidationError("Square side length must be a positive, finite number of metres.")
    if not isfinite(hover_duration_s) or hover_duration_s <= 0.0:
        raise MissionValidationError("Hover duration must be a positive, finite number of seconds.")
    if not isfinite(waypoint_tolerance_m) or waypoint_tolerance_m <= 0.0:
        raise MissionValidationError("Waypoint tolerance must be a positive, finite number of metres.")
    if not isfinite(waypoint_timeout_s) or waypoint_timeout_s <= 0.0:
        raise MissionValidationError("Waypoint timeout must be a positive, finite number of seconds.")

    takeoff = TakeoffCommand(target_altitude_m=takeoff_altitude_m)
    legs = tuple(
        WaypointCommand(north_m=north_m, east_m=east_m, target_altitude_m=waypoint_altitude_m)
        for north_m, east_m in (
            (side_length_m, 0.0),
            (0.0, side_length_m),
            (-side_length_m, 0.0),
            (0.0, -side_length_m),
        )
    )
    # Safety limits are launch-relative, whereas execution legs are relative
    # to the current position. Evaluate the cumulative corner coordinates
    # before arming so that a rejected later corner blocks the whole mission.
    absolute_corners = (
        WaypointCommand(side_length_m, 0.0, waypoint_altitude_m),
        WaypointCommand(side_length_m, side_length_m, waypoint_altitude_m),
        WaypointCommand(0.0, side_length_m, waypoint_altitude_m),
        WaypointCommand(0.0, 0.0, waypoint_altitude_m),
    )
    gate.evaluate(takeoff)
    for waypoint in absolute_corners:
        gate.evaluate(waypoint)
    return TakeoffWaypointSquareLandMission(
        takeoff=takeoff,
        waypoints=(legs[0], legs[1], legs[2], legs[3]),
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
