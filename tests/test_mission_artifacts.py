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

        self.assertEqual(artifact.version, "v0.1")
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
                    "recorded_at": "2026-07-16T12:30:00Z",
                    "run_id": "mission-01",
                    "version": "v0.1",
                },
            )

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
