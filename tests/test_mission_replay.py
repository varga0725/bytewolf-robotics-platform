"""Offline replay coverage for versioned mission audit artifacts."""

from datetime import UTC, datetime
import json
from pathlib import Path
import tempfile
import unittest

from brain.mission.artifacts import MissionArtifactWriter, MissionAuditArtifact, MissionTelemetrySnapshot
from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.replay import MissionReplayError, replay_artifact


class MissionReplayTests(unittest.TestCase):
    def test_replays_a_completed_artifact_without_a_flight_adapter(self) -> None:
        started_at = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
        execution = (
            MissionExecution.empty()
            .transition(MissionPhase.ARMING, started_at)
            .transition(MissionPhase.TAKING_OFF, datetime(2026, 7, 18, 10, 0, 5, tzinfo=UTC))
            .transition(MissionPhase.LANDING, datetime(2026, 7, 18, 10, 1, tzinfo=UTC))
            .transition(MissionPhase.COMPLETED, datetime(2026, 7, 18, 10, 1, 8, tzinfo=UTC))
        )
        artifact = MissionAuditArtifact.from_execution(
            "replayable-run",
            execution,
            recorded_at=datetime(2026, 7, 18, 10, 1, 9, tzinfo=UTC),
            safety_decision="approved",
            outcome="completed",
            telemetry=MissionTelemetrySnapshot(
                captured_at=started_at,
                navigation_ready=True,
                home_position_valid=True,
                global_position_valid=True,
                battery_percent=82.5,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = MissionArtifactWriter(Path(directory)).write(artifact)
            replay = replay_artifact(path)

        self.assertEqual(replay.run_id, "replayable-run")
        self.assertEqual(replay.outcome, "completed")
        self.assertEqual(replay.terminal_phase, MissionPhase.COMPLETED)
        self.assertEqual(replay.events, execution.events)
        self.assertEqual(replay.preflight_battery_percent, 82.5)

    def test_preserves_failed_run_diagnostics_for_offline_analysis(self) -> None:
        artifact = MissionAuditArtifact.from_execution(
            "failed-run",
            MissionExecution.empty().transition(
                MissionPhase.ARMING, datetime(2026, 7, 18, 10, 0, tzinfo=UTC)
            ).transition(MissionPhase.FAILED, datetime(2026, 7, 18, 10, 0, 2, tzinfo=UTC)),
            recorded_at=datetime(2026, 7, 18, 10, 0, 3, tzinfo=UTC),
            safety_decision="approved",
            outcome="failed",
            failure_reason="RuntimeError: telemetry_unavailable; land fallback attempted",
        )

        with tempfile.TemporaryDirectory() as directory:
            path = MissionArtifactWriter(Path(directory)).write(artifact)
            replay = replay_artifact(path)

        self.assertEqual(replay.terminal_phase, MissionPhase.FAILED)
        self.assertEqual(replay.failure_reason, artifact.failure_reason)
        self.assertIsNone(replay.preflight_battery_percent)

    def test_rejects_corrupt_or_out_of_order_artifacts(self) -> None:
        document = {
            "events": [
                {"phase": "arming", "timestamp": "2026-07-18T10:00:05Z"},
                {"phase": "taking_off", "timestamp": "2026-07-18T10:00:00Z"},
            ],
            "failure_reason": None,
            "outcome": "failed",
            "recorded_at": "2026-07-18T10:00:06Z",
            "run_id": "corrupt-run",
            "safety_decision": "approved",
            "telemetry": None,
            "version": "v0.2",
        }

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "corrupt-run.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaisesRegex(MissionReplayError, "out of chronological order"):
                replay_artifact(path)

    def test_rejects_an_impossible_but_chronological_phase_sequence(self) -> None:
        document = {
            "events": [
                {"phase": "arming", "timestamp": "2026-07-18T10:00:00Z"},
                {"phase": "completed", "timestamp": "2026-07-18T10:00:01Z"},
            ],
            "failure_reason": None,
            "outcome": "completed",
            "recorded_at": "2026-07-18T10:00:02Z",
            "run_id": "impossible-run",
            "safety_decision": "approved",
            "telemetry": None,
            "version": "v0.2",
        }

        with self.assertRaisesRegex(MissionReplayError, "state machine"):
            from brain.mission.replay import replay_document

            replay_document(document)


if __name__ == "__main__":
    unittest.main()
