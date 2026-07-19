"""Tests for routing compiled MissionSpec contracts to approved flight paths."""

import unittest

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.mission.execution import MissionExecution
from brain.mission_spec.orchestrator import (
    MissionSpecExecutionError,
    execute_compiled_mission,
)
from brain.mission_spec.validation import CompiledMission


def compiled(*commands: object, holds: tuple[float, ...] = (3.0,)) -> CompiledMission:
    return CompiledMission(
        mission_id="test-mission",
        vehicle_id="x500v2_reference_01",
        source_hash="a" * 64,
        commands=commands,  # type: ignore[arg-type]
        hold_durations_s=holds,
        terminal_action="LAND" if isinstance(commands[-1], LandCommand) else "RTL",
        abort_policy=(),
    )


class RecordingAdapter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    async def execute(self, mission: object) -> MissionExecution:
        self.calls.append(("land", mission))
        return MissionExecution.empty()

    async def execute_waypoint_mission(self, mission: object) -> MissionExecution:
        self.calls.append(("waypoint_land", mission))
        return MissionExecution.empty()

    async def execute_waypoints_mission(self, mission: object) -> MissionExecution:
        self.calls.append(("waypoints_land", mission))
        return MissionExecution.empty()

    async def execute_return_to_home_mission(self, mission: object) -> MissionExecution:
        self.calls.append(("return_to_home", mission))
        return MissionExecution.empty()


class MissionSpecOrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_a_compiled_takeoff_hold_land_to_the_land_path(self) -> None:
        adapter = RecordingAdapter()

        await execute_compiled_mission(
            adapter, compiled(TakeoffCommand(2.0), LandCommand())
        )

        self.assertEqual(adapter.calls[0][0], "land")

    async def test_routes_a_compiled_waypoint_land_to_the_waypoint_path(self) -> None:
        adapter = RecordingAdapter()

        await execute_compiled_mission(
            adapter,
            compiled(
                TakeoffCommand(2.0),
                WaypointCommand(5.0, 0.0, 2.0),
                LandCommand(),
            ),
        )

        self.assertEqual(adapter.calls[0][0], "waypoint_land")

    async def test_routes_multiple_launch_relative_waypoints_as_safe_legs(self) -> None:
        adapter = RecordingAdapter()
        await execute_compiled_mission(adapter, compiled(TakeoffCommand(2.0), WaypointCommand(5, 0, 2), WaypointCommand(5, 5, 2), LandCommand()))
        self.assertEqual(adapter.calls[0][0], "waypoints_land")
        self.assertEqual(adapter.calls[0][1].waypoints, (WaypointCommand(5, 0, 2), WaypointCommand(0, 5, 2)))

    async def test_routes_a_compiled_takeoff_hold_rtl_to_the_return_path(self) -> None:
        adapter = RecordingAdapter()

        await execute_compiled_mission(
            adapter, compiled(TakeoffCommand(2.0), ReturnToHomeCommand(2.0))
        )

        self.assertEqual(adapter.calls[0][0], "return_to_home")

    async def test_rejects_an_unsupported_compiled_shape_before_calling_the_adapter(self) -> None:
        adapter = RecordingAdapter()
        mission = compiled(
            TakeoffCommand(2.0),
            WaypointCommand(5.0, 0.0, 2.0),
            ReturnToHomeCommand(2.0),
        )

        with self.assertRaisesRegex(MissionSpecExecutionError, "unsupported"):
            await execute_compiled_mission(adapter, mission)

        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
