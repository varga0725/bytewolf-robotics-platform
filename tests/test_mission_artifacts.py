from datetime import UTC, datetime
from dataclasses import FrozenInstanceError
from pathlib import Path
import json
import tempfile
import unittest

from brain.mission.artifacts import (
    DEFAULT_MISSION_RUNS_DIRECTORY,
    MissionAuditArtifact,
    MissionArtifactWriter,
    MissionTelemetrySnapshot,
)
from brain.mission.execution import MissionExecution, MissionPhase


class MissionArtifactTests(unittest.TestCase):
    def test_creates_an_immutable_versioned_artifact_from_an_execution(self) -> None:
        timestamp = datetime(2026, 7, 16, 12, 30, tzinfo=UTC)
        execution = MissionExecution.empty().transition(MissionPhase.ARMING, timestamp)

        artifact = MissionAuditArtifact.from_execution(
            run_id="mission-20260716T123000Z",
            execution=execution,
            recorded_at=timestamp,
        )

        self.assertEqual(artifact.version, "v0.2")
        self.assertEqual(artifact.run_id, "mission-20260716T123000Z")
        self.assertEqual(artifact.recorded_at, timestamp)
        self.assertEqual(artifact.events[0].phase, MissionPhase.ARMING)
        self.assertIsInstance(artifact.events, tuple)
        with self.assertRaises(FrozenInstanceError):
            artifact.run_id = "replacement"  # type: ignore[misc]

    def test_writer_persists_a_canonical_json_artifact_without_mutating_it(self) -> None:
        timestamp = datetime(2026, 7, 16, 12, 30, tzinfo=UTC)
        execution = MissionExecution.empty().transition(MissionPhase.ARMING, timestamp)
        artifact = MissionAuditArtifact.from_execution("mission-01", execution, timestamp)

        with tempfile.TemporaryDirectory() as directory:
            output_path = MissionArtifactWriter(Path(directory)).write(artifact)

            self.assertEqual(output_path, Path(directory) / "mission-01.json")
            self.assertEqual(artifact.events, execution.events)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8")),
                {
                    "events": [
                        {
                            "phase": "arming",
                            "timestamp": "2026-07-16T12:30:00Z",
                        }
                    ],
                    "failure_reason": None,
                    "outcome": "failed",
                    "recorded_at": "2026-07-16T12:30:00Z",
                    "run_id": "mission-01",
                    "safety_decision": "not-evaluated",
                    "telemetry": None,
                    "version": "v0.2",
                },
            )

    def test_captures_immutable_execution_outcome_and_preflight_telemetry(self) -> None:
        timestamp = datetime(2026, 7, 16, 12, 30, tzinfo=UTC)
        telemetry = MissionTelemetrySnapshot(
            captured_at=timestamp,
            navigation_ready=True,
            home_position_valid=True,
            global_position_valid=True,
            battery_percent=75.0,
        )

        artifact = MissionAuditArtifact.from_execution(
            "mission-telemetry",
            MissionExecution.empty(),
            timestamp,
            safety_decision="approved",
            outcome="failed",
            failure_reason="TimeoutError: PX4 landing confirmation timed out.",
            telemetry=telemetry,
        )

        self.assertEqual(
            artifact.to_document()["telemetry"],
            {
                "battery_percent": 75.0,
                "captured_at": "2026-07-16T12:30:00Z",
                "global_position_valid": True,
                "home_position_valid": True,
                "navigation_ready": True,
            },
        )
        self.assertEqual(artifact.to_document()["safety_decision"], "approved")
        self.assertEqual(artifact.to_document()["outcome"], "failed")
        self.assertIn("TimeoutError", artifact.to_document()["failure_reason"])

    def test_writer_rejects_a_run_id_that_could_escape_its_output_directory(self) -> None:
        artifact = MissionAuditArtifact.from_execution(
            "../outside",
            MissionExecution.empty(),
            datetime(2026, 7, 16, tzinfo=UTC),
        )

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "run_id"):
                MissionArtifactWriter(Path(directory)).write(artifact)

    def test_default_writer_directory_is_the_repository_mission_runs_path(self) -> None:
        self.assertEqual(MissionArtifactWriter().directory, DEFAULT_MISSION_RUNS_DIRECTORY)


if __name__ == "__main__":
    unittest.main()
