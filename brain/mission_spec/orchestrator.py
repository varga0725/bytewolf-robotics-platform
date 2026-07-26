"""Safe routing from compiled MissionSpec contracts to approved MAVSDK mission paths."""

from collections.abc import Awaitable
from math import isfinite
from typing import Protocol

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.mission.execution import MissionExecution
from brain.mission.flight import (
    TakeoffHoverLandMission,
    TakeoffReturnToHomeMission,
    TakeoffWaypointLandMission,
    TakeoffWaypointsLandMission,
    TakeoffWaypointsReturnToHomeMission,
)
from brain.mission_spec.validation import CompiledMission


class MissionSpecExecutionError(ValueError):
    """Raised when a compiled mission cannot use an approved execution path."""


class ApprovedMissionAdapter(Protocol):
    """The bounded adapter operations that MissionSpec may select between."""

    def execute(self, mission: TakeoffHoverLandMission) -> Awaitable[MissionExecution]: ...

    def execute_waypoint_mission(
        self, mission: TakeoffWaypointLandMission
    ) -> Awaitable[MissionExecution]: ...

    def execute_waypoints_mission(self, mission: TakeoffWaypointsLandMission) -> Awaitable[MissionExecution]: ...

    def execute_return_to_home_mission(
        self, mission: TakeoffReturnToHomeMission
    ) -> Awaitable[MissionExecution]: ...

    def execute_waypoints_return_to_home_mission(
        self, mission: TakeoffWaypointsReturnToHomeMission
    ) -> Awaitable[MissionExecution]: ...


async def execute_compiled_mission(
    adapter: ApprovedMissionAdapter, mission: CompiledMission
) -> MissionExecution:
    """Execute only the small, lossless MissionSpec subset supported by the adapter.

    Compilation remains the authorization boundary.  This bridge deliberately rejects
    shapes which cannot be represented by the existing bounded mission classes, so no
    approved command or hold duration can be silently dropped.
    """
    takeoff, intermediate, terminal, hold_duration_s = _supported_shape(mission)

    if isinstance(terminal, LandCommand):
        if not intermediate:
            return await adapter.execute(
                TakeoffHoverLandMission(takeoff=takeoff, hover_duration_s=hold_duration_s)
            )
        if len(intermediate) == 1:
            return await adapter.execute_waypoint_mission(
            TakeoffWaypointLandMission(
                takeoff=takeoff,
                waypoint=intermediate[0],
                hover_duration_s=hold_duration_s,
            )
            )
        return await adapter.execute_waypoints_mission(
            TakeoffWaypointsLandMission(takeoff=takeoff, waypoints=_route_legs(intermediate), hover_duration_s=hold_duration_s)
        )

    if intermediate:
        return await adapter.execute_waypoints_return_to_home_mission(
            TakeoffWaypointsReturnToHomeMission(
                takeoff=takeoff,
                waypoints=_route_legs(intermediate),
                hover_duration_s=hold_duration_s,
                return_to_home=terminal,
            )
        )

    return await adapter.execute_return_to_home_mission(
        TakeoffReturnToHomeMission(
            takeoff=takeoff,
            hover_duration_s=hold_duration_s,
            return_to_home=terminal,
        )
    )


def require_executable_mission(mission: CompiledMission) -> None:
    """Fail closed before connection when a MissionSpec has no lossless route."""
    _supported_shape(mission)


def _supported_shape(
    mission: CompiledMission,
) -> tuple[TakeoffCommand, tuple[WaypointCommand, ...], LandCommand | ReturnToHomeCommand, float]:
    """Return a lossless executable shape or reject it before any adapter operation."""
    commands = mission.commands
    if len(commands) < 2 or not isinstance(commands[0], TakeoffCommand):
        raise MissionSpecExecutionError("unsupported compiled MissionSpec: expected TAKEOFF first.")

    terminal = commands[-1]
    if not isinstance(terminal, (LandCommand, ReturnToHomeCommand)):
        raise MissionSpecExecutionError("unsupported compiled MissionSpec: expected LAND or RTL terminal.")
    expected_terminal_action = "LAND" if isinstance(terminal, LandCommand) else "RTL"
    if mission.terminal_action != expected_terminal_action:
        raise MissionSpecExecutionError("unsupported compiled MissionSpec: terminal action is inconsistent.")

    # A mission may hold once or not at all. An area sweep goes from its last
    # waypoint straight to the return, and requiring a HOLD there would mean
    # either refusing a mission the operator did ask for, or inserting a wait in
    # the air that they did not.
    if len(mission.hold_durations_s) > 1:
        raise MissionSpecExecutionError(
            "unsupported compiled MissionSpec: expected at most one HOLD duration."
        )
    hold_duration_s = mission.hold_durations_s[0] if mission.hold_durations_s else 0.0
    if not isfinite(hold_duration_s) or hold_duration_s < 0.0:
        raise MissionSpecExecutionError("unsupported compiled MissionSpec: HOLD duration must not be negative.")

    intermediate = commands[1:-1]
    if any(not isinstance(command, WaypointCommand) for command in intermediate):
        raise MissionSpecExecutionError(
            "unsupported compiled MissionSpec: only local waypoints may sit between takeoff and the terminal."
        )
    # Deliberately uncapped here. How many waypoints a mission may carry is
    # already decided in three places that know something this bridge does not:
    # the survey pattern's own waypoint bound, the SafetyGate's per-waypoint
    # geofence and radius check, and the runtime battery watchdog. A fourth
    # number here would be a second source of a limit — and the one most likely
    # to drift from the others.
    waypoints = tuple(command for command in intermediate if isinstance(command, WaypointCommand))
    return commands[0], waypoints, terminal, hold_duration_s


def _route_legs(points: tuple[WaypointCommand, ...]) -> tuple[WaypointCommand, ...]:
    north = east = 0.0
    legs: list[WaypointCommand] = []
    for point in points:
        legs.append(WaypointCommand(point.north_m - north, point.east_m - east, point.target_altitude_m))
        north, east = point.north_m, point.east_m
    return tuple(legs)
