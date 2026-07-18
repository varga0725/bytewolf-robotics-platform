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

    def execute_return_to_home_mission(
        self, mission: TakeoffReturnToHomeMission
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
        return await adapter.execute_waypoint_mission(
            TakeoffWaypointLandMission(
                takeoff=takeoff,
                waypoint=intermediate[0],
                hover_duration_s=hold_duration_s,
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

    if len(mission.hold_durations_s) != 1:
        raise MissionSpecExecutionError(
            "unsupported compiled MissionSpec: expected exactly one HOLD duration."
        )
    hold_duration_s = mission.hold_durations_s[0]
    if not isfinite(hold_duration_s) or hold_duration_s <= 0.0:
        raise MissionSpecExecutionError("unsupported compiled MissionSpec: HOLD duration must be positive.")

    intermediate = commands[1:-1]
    if len(intermediate) > 1 or any(not isinstance(command, WaypointCommand) for command in intermediate):
        raise MissionSpecExecutionError(
            "unsupported compiled MissionSpec: only one local waypoint is supported."
        )
    waypoints = tuple(command for command in intermediate if isinstance(command, WaypointCommand))
    if isinstance(terminal, ReturnToHomeCommand) and waypoints:
        raise MissionSpecExecutionError(
            "unsupported compiled MissionSpec: RTL after a waypoint has no approved adapter path."
        )
    return commands[0], waypoints, terminal, hold_duration_s
