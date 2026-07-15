from datetime import UTC, datetime
import unittest

from brain.mission.execution import (
    MissionExecution,
    MissionPhase,
    MissionTransitionError,
)


class MissionExecutionTests(unittest.TestCase):
    def test_records_a_valid_takeoff_hover_land_lifecycle(self) -> None:
        execution = MissionExecution.empty()
        timestamp = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)

        for phase in (
            MissionPhase.ARMING,
            MissionPhase.TAKING_OFF,
            MissionPhase.HOVERING,
            MissionPhase.LANDING,
            MissionPhase.COMPLETED,
        ):
            execution = execution.transition(phase, timestamp=timestamp)

        self.assertEqual(execution.phase, MissionPhase.COMPLETED)
        self.assertEqual(
            tuple(event.phase for event in execution.events),
            (
                MissionPhase.ARMING,
                MissionPhase.TAKING_OFF,
                MissionPhase.HOVERING,
                MissionPhase.LANDING,
                MissionPhase.COMPLETED,
            ),
        )
        self.assertEqual(execution.events[0].timestamp, timestamp)

    def test_rejects_an_invalid_state_transition(self) -> None:
        with self.assertRaises(MissionTransitionError):
            MissionExecution.empty().transition(MissionPhase.HOVERING)

    def test_returns_a_new_immutable_execution_for_each_event(self) -> None:
        initial = MissionExecution.empty()
        updated = initial.transition(MissionPhase.ARMING)

        self.assertEqual(initial.events, ())
        self.assertEqual(updated.phase, MissionPhase.ARMING)
        self.assertIsInstance(updated.events, tuple)
