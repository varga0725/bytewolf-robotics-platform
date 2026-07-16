"""Safety and execution coverage for the bounded four-point square mission."""

import unittest

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli import fly_waypoint_square_land
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import (
    TakeoffWaypointSquareLandMission,
    authorize_takeoff_waypoint_square_land,
)
from brain.navigation.waypoints import GlobalPosition
from brain.safety.gate import FlightLimits, SafetyGate, SafetyViolation


class _Action:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    async def set_takeoff_altitude(self, altitude_m: float) -> None:
        self.events.append(("set_takeoff_altitude", altitude_m))

    async def arm(self) -> None:
        self.events.append("arm")

    async def takeoff(self) -> None:
        self.events.append("takeoff")

    async def land(self) -> None:
        self.events.append("land")


class _Drone:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.action = _Action(self.events)


class _RecordingAdapter(MavsdkMissionAdapter):
    def __init__(self, fail_at_waypoint: int | None = None) -> None:
        self.drone = _Drone()
        super().__init__(self.drone, sleep=self._sleep)
        self.commands: list[object] = []
        self.confirmed: list[GlobalPosition] = []
        self.local_position_m = (0.0, 0.0)
        self.fail_at_waypoint = fail_at_waypoint

    async def _sleep(self, _seconds: float) -> None:
        return None

    async def _require_preflight(self) -> None:
        return None

    async def goto_relative_waypoint(self, command):  # type: ignore[no-untyped-def]
        self.commands.append(command)
        if self.fail_at_waypoint == len(self.commands):
            raise RuntimeError("waypoint rejected")
        north_m, east_m = self.local_position_m
        self.local_position_m = (north_m + command.north_m, east_m + command.east_m)
        return GlobalPosition(self.local_position_m[0], self.local_position_m[1], 120.0)

    async def wait_until_waypoint_reached(self, target, tolerance_m, timeout_s):  # type: ignore[no-untyped-def]
        self.confirmed.append(target)

    async def _normal_land(self, execution: MissionExecution, _timeout_s: float) -> MissionExecution:
        await self.drone.action.land()
        return execution.transition(MissionPhase.LANDING).transition(MissionPhase.COMPLETED)

    async def _fallback_land_after_airborne_failure(self, execution, airborne, timeout_s):  # type: ignore[no-untyped-def]
        if airborne:
            await self.drone.action.land()
        return execution


class WaypointSquareMissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.gate = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=50.0))

    def test_authorizes_each_corner_in_deterministic_order(self) -> None:
        mission = authorize_takeoff_waypoint_square_land(
            self.gate,
            takeoff_altitude_m=2.0,
            side_length_m=5.0,
            waypoint_altitude_m=2.0,
            hover_duration_s=3.0,
        )

        self.assertIsInstance(mission, TakeoffWaypointSquareLandMission)
        self.assertEqual(
            tuple((waypoint.north_m, waypoint.east_m) for waypoint in mission.waypoints),
            ((5.0, 0.0), (0.0, 5.0), (-5.0, 0.0), (0.0, -5.0)),
        )

    def test_rejects_the_entire_square_when_any_corner_exceeds_the_safety_radius(self) -> None:
        with self.assertRaises(SafetyViolation):
            authorize_takeoff_waypoint_square_land(
                SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=6.0)),
                takeoff_altitude_m=2.0,
                side_length_m=5.0,
                waypoint_altitude_m=2.0,
                hover_duration_s=3.0,
            )

    def test_cli_uses_the_read_only_live_dashboard_by_default(self) -> None:
        arguments = fly_waypoint_square_land.parse_arguments(())

        self.assertEqual(arguments.side_length, 5.0)
        self.assertEqual(
            arguments.dashboard_snapshot,
            __import__("pathlib").Path("simulation/artifacts/dashboard/live-telemetry.json"),
        )

    async def test_visits_and_confirms_every_corner_before_landing(self) -> None:
        mission = authorize_takeoff_waypoint_square_land(
            self.gate, 2.0, 5.0, 2.0, 3.0
        )
        adapter = _RecordingAdapter()

        execution = await adapter.execute_waypoint_square_mission(mission)

        self.assertEqual(adapter.commands, list(mission.waypoints))
        self.assertEqual(
            tuple((target.latitude_deg, target.longitude_deg) for target in adapter.confirmed),
            ((5.0, 0.0), (5.0, 5.0), (0.0, 5.0), (0.0, 0.0)),
        )
        self.assertEqual(len(adapter.confirmed), 4)
        self.assertEqual(adapter.drone.events.count("land"), 1)
        self.assertEqual(execution.events[-1].phase, MissionPhase.COMPLETED)

    async def test_lands_once_when_a_later_corner_fails(self) -> None:
        mission = authorize_takeoff_waypoint_square_land(
            self.gate, 2.0, 5.0, 2.0, 3.0
        )
        adapter = _RecordingAdapter(fail_at_waypoint=3)

        with self.assertRaisesRegex(RuntimeError, "waypoint rejected"):
            await adapter.execute_waypoint_square_mission(mission)

        self.assertEqual(adapter.drone.events.count("land"), 1)
