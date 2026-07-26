"""Coverage for deliberate, bounded interruption safety paths."""

import unittest

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli import fly_controlled_interruption
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import (
    InterruptionAction,
    authorize_takeoff_interrupt_land,
)
from brain.safety.gate import FlightLimits, SafetyGate
from simulation.scenarios.scenarios import P0_V2_SCENARIOS


class _Action:
    def __init__(self, events: list[object]) -> None:
        self._events = events

    async def set_takeoff_altitude(self, altitude_m: float) -> None:
        self._events.append(("set_takeoff_altitude", altitude_m))

    async def arm(self) -> None:
        self._events.append("arm")

    async def takeoff(self) -> None:
        self._events.append("takeoff")

    async def hold(self) -> None:
        self._events.append("hold")

    async def land(self) -> None:
        self._events.append("land")


class _Drone:
    def __init__(self) -> None:
        self.events: list[object] = []
        self.action = _Action(self.events)


class _Adapter(MavsdkMissionAdapter):
    def __init__(self) -> None:
        self.drone = _Drone()
        super().__init__(self.drone, sleep=self._sleep)
        self.sleeps: list[float] = []

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    async def _require_preflight(self) -> None:
        return None

    async def _normal_land(
        self, execution: MissionExecution, _timeout_s: float
    ) -> MissionExecution:
        await self.drone.action.land()
        return execution.transition(MissionPhase.LANDING).transition(MissionPhase.COMPLETED)

    async def _fallback_land_after_airborne_failure(self, execution, airborne, timeout_s):  # type: ignore[no-untyped-def]
        if airborne:
            await self.drone.action.land()
        return execution


class ControlledInterruptionMissionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.gate = SafetyGate(FlightLimits(max_altitude_m=20.0, max_distance_m=50.0))

    def test_cli_requires_an_explicit_interruption_action(self) -> None:
        with self.assertRaises(SystemExit):
            fly_controlled_interruption.parse_arguments(())

        arguments = fly_controlled_interruption.parse_arguments(("--interruption-action", "hold"))

        self.assertEqual(arguments.interruption_action, "hold")

    def test_p0_v2_matrix_contains_both_controlled_interruption_outcomes(self) -> None:
        scenarios = {scenario.identifier: scenario for scenario in P0_V2_SCENARIOS}

        self.assertIn("mission-interrupt-hold-cleanup-land", scenarios)
        self.assertIn("mission-interrupt-land", scenarios)
        self.assertIn("hold", scenarios["mission-interrupt-hold-cleanup-land"].arguments)
        self.assertIn("land", scenarios["mission-interrupt-land"].arguments)

    async def test_hold_interruption_is_recorded_then_ends_with_cleanup_land(self) -> None:
        mission = authorize_takeoff_interrupt_land(
            self.gate,
            takeoff_altitude_m=2.0,
            interrupt_after_s=3.0,
            interruption_action=InterruptionAction.HOLD,
            hold_cleanup_s=1.0,
        )
        adapter = _Adapter()

        execution = await adapter.execute_controlled_interruption_mission(mission)

        self.assertEqual(adapter.drone.events, [("set_takeoff_altitude", 2.0), "arm", "takeoff", "hold", "land"])
        self.assertEqual(adapter.sleeps, [3.0, 1.0])
        self.assertEqual(
            tuple(event.phase for event in execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.HOVERING,
                MissionPhase.HOLDING,
                MissionPhase.LANDING,
                MissionPhase.COMPLETED,
            ),
        )

    async def test_land_interruption_lands_without_a_hold_command(self) -> None:
        mission = authorize_takeoff_interrupt_land(
            self.gate,
            takeoff_altitude_m=2.0,
            interrupt_after_s=3.0,
            interruption_action=InterruptionAction.LAND,
        )
        adapter = _Adapter()

        execution = await adapter.execute_controlled_interruption_mission(mission)

        self.assertEqual(adapter.drone.events, [("set_takeoff_altitude", 2.0), "arm", "takeoff", "land"])
        self.assertEqual(adapter.sleeps, [3.0])
        self.assertNotIn(MissionPhase.HOLDING, tuple(event.phase for event in execution.events))
        self.assertEqual(execution.phase, MissionPhase.COMPLETED)
