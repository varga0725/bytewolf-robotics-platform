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

    async def execute_waypoints_return_to_home_mission(self, mission: object) -> MissionExecution:
        self.calls.append(("waypoints_return_to_home", mission))
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

    async def test_a_route_that_ends_at_home_takes_the_route_and_return_path(self) -> None:
        """Going somewhere and then coming home is not landing where you ended up.

        This shape used to be refused for want of an adapter path, which left
        every map-picked target and every area sweep reviewable, approvable, and
        unflyable.
        """
        adapter = RecordingAdapter()

        await execute_compiled_mission(
            adapter,
            compiled(TakeoffCommand(2.0), WaypointCommand(5.0, 0.0, 2.0), ReturnToHomeCommand(2.0)),
        )

        self.assertEqual(adapter.calls[0][0], "waypoints_return_to_home")

    async def test_a_sweep_with_no_hold_flies_without_a_hover_nobody_asked_for(self) -> None:
        adapter = RecordingAdapter()

        await execute_compiled_mission(
            adapter,
            compiled(
                TakeoffCommand(4.0),
                WaypointCommand(5.0, 0.0, 4.0),
                WaypointCommand(5.0, 10.0, 4.0),
                ReturnToHomeCommand(2.0),
                holds=(),
            ),
        )

        name, mission = adapter.calls[0]
        self.assertEqual(name, "waypoints_return_to_home")
        self.assertEqual(mission.hover_duration_s, 0.0)

    async def test_a_route_longer_than_four_legs_is_no_longer_capped_here(self) -> None:
        """The bound belongs to the survey pattern, the gate, and the watchdog.

        A fifth copy of it in this bridge is the one most likely to drift from
        the other three.
        """
        adapter = RecordingAdapter()
        legs = tuple(WaypointCommand(float(index), 0.0, 4.0) for index in range(1, 13))

        await execute_compiled_mission(
            adapter, compiled(TakeoffCommand(4.0), *legs, ReturnToHomeCommand(2.0), holds=())
        )

        self.assertEqual(len(adapter.calls[0][1].waypoints), 12)

    async def test_a_second_hold_is_still_refused_before_the_adapter_is_called(self) -> None:
        adapter = RecordingAdapter()

        with self.assertRaisesRegex(MissionSpecExecutionError, "at most one HOLD"):
            await execute_compiled_mission(
                adapter,
                compiled(TakeoffCommand(2.0), LandCommand(), holds=(3.0, 4.0)),
            )

        self.assertEqual(adapter.calls, [])

    async def test_a_non_waypoint_between_takeoff_and_the_terminal_is_refused(self) -> None:
        adapter = RecordingAdapter()

        with self.assertRaisesRegex(MissionSpecExecutionError, "only local waypoints"):
            await execute_compiled_mission(
                adapter,
                compiled(TakeoffCommand(2.0), TakeoffCommand(3.0), LandCommand()),
            )

        self.assertEqual(adapter.calls, [])


if __name__ == "__main__":
    unittest.main()
