"""Recording evidence must never endanger the run that produced it.

World memory is derived and perishable; a mission's audit artifact and a
scenario's report are the records of account. So a failed append is reported,
never raised, and it always happens after the authoritative file is safe.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.memory.recorder import WorldMemoryRecorder
from brain.memory.world_map import MapGrid, VehiclePose
from brain.memory.world_memory import load_world_memory
from brain.mission.artifacts import MissionAuditArtifact
from brain.mission.execution import MissionExecution
from brain.perception.target_estimator import TargetObservation
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
GRID = MapGrid(47.397971, 8.546164, cell_size_m=2.0)
POSE = VehiclePose(47.397971, 8.546164, yaw_deg=0.0)


def _scan_document(observed_at: datetime = NOW) -> dict[str, object]:
    return {
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": observed_at.isoformat(),
        "max_age_s": 5.0,
        "validity": "valid",
        "source": "gz lidar_2d_v2",
        "payload": {
            "frame": "body_frd",
            "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
            "sectors": [
                {"yaw_deg": 0.0, "width_deg": 10.0, "coverage": "measured", "distance_m": 9.0, "confidence": 0.9},
                {"yaw_deg": 180.0, "width_deg": 90.0, "coverage": "unobserved"},
            ],
        },
    }


def _obstacle_observation(observed_at: datetime = NOW):
    return load_observation(_scan_document(observed_at))


def _target(validity: str = "valid") -> TargetObservation:
    return TargetObservation(
        captured_at=NOW,
        max_age_s=0.5,
        declared_validity=validity,
        label="landing-pad",
        confidence=0.9,
        offset_north_m=1.0,
        offset_east_m=2.0,
        range_m=8.0,
        horizontal_uncertainty_m=0.2,
        global_fix=None,
        source="camera:down_rgb",
    )


class RecorderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "world" / "claims.jsonl"
        self.recorder = WorldMemoryRecorder(self.path)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_scan_with_a_pose_is_remembered_as_sectors_and_map_cells(self) -> None:
        result = self.recorder.record_obstacle_scan(
            _obstacle_observation(), NOW, pose=POSE, grid=GRID, artifact="report.json"
        )

        categories = {claim.category for claim in load_world_memory(self.path).recall(NOW)}
        self.assertTrue(result.complete)
        self.assertEqual(categories, {"obstacle", "map_region"})

    def test_a_scan_without_a_pose_is_remembered_but_never_placed_on_the_map(self) -> None:
        self.recorder.record_obstacle_scan(_obstacle_observation(), NOW)

        categories = {claim.category for claim in load_world_memory(self.path).recall(NOW)}
        self.assertEqual(categories, {"obstacle"}, "a wall cannot be placed without knowing where we were")

    def test_an_unusable_sighting_writes_nothing(self) -> None:
        result = self.recorder.record_target_sighting(_target("invalid"), NOW, subject="marker:red-pad")

        self.assertEqual(result.written, 0)
        self.assertFalse(self.path.exists())

    def test_a_mission_outcome_is_remembered_with_its_artifact_path(self) -> None:
        artifact = MissionAuditArtifact.from_execution(
            "3f6c2b41-0d3f-4f0e-8b4a-6c0f3a2d9e77", MissionExecution.empty(), recorded_at=NOW, outcome="completed"
        )

        self.recorder.record_mission_outcome(artifact, artifact_path="var/mission-runs/x.json")

        claims = load_world_memory(self.path).recall(NOW)
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].artifact, "var/mission-runs/x.json")

    def test_an_unwritable_log_is_reported_rather_than_raised(self) -> None:
        blocked = Path(self.directory.name) / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")
        recorder = WorldMemoryRecorder(blocked / "claims.jsonl")

        result = recorder.record_obstacle_scan(_obstacle_observation(), NOW, pose=POSE, grid=GRID)

        self.assertEqual(result.written, 0)
        self.assertIsNotNone(result.failure)
        self.assertFalse(result.complete)

    def test_a_burst_of_claims_is_capped_and_the_drop_is_counted(self) -> None:
        recorder = WorldMemoryRecorder(self.path, max_claims_per_call=1)

        result = recorder.record_obstacle_scan(_obstacle_observation(), NOW, pose=POSE, grid=GRID)

        self.assertEqual(result.written, 1)
        self.assertEqual(result.dropped, 1)
        self.assertFalse(result.complete, "a silent cap would read as full coverage")


class MissionArtifactWiringTests(unittest.TestCase):
    """The CLI seam records the run only after its audit artifact exists."""

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_writing_a_run_artifact_also_remembers_the_outcome(self) -> None:
        from brain.cli.artifacts import write_run_artifact

        world_path = self.root / "world" / "claims.jsonl"
        artifact_path = write_run_artifact(
            self.root / "runs",
            MissionExecution.empty(),
            "approved",
            "completed",
            None,
            world_recorder=WorldMemoryRecorder(world_path),
        )

        claims = load_world_memory(world_path).recall(datetime.now(UTC))
        self.assertTrue(artifact_path.is_file())
        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].category, "mission_outcome")
        self.assertEqual(claims[0].artifact, str(artifact_path))

    def test_without_a_recorder_a_run_remembers_nothing(self) -> None:
        from brain.cli.artifacts import write_run_artifact

        artifact_path = write_run_artifact(
            self.root / "runs", MissionExecution.empty(), "approved", "completed", None
        )

        self.assertTrue(artifact_path.is_file())
        self.assertFalse((self.root / "world").exists())

    def test_a_failing_recorder_cannot_cost_the_run_its_audit_artifact(self) -> None:
        from brain.cli.artifacts import write_run_artifact

        blocked = self.root / "blocked"
        blocked.write_text("not a directory", encoding="utf-8")

        artifact_path = write_run_artifact(
            self.root / "runs",
            MissionExecution.empty(),
            "approved",
            "completed",
            None,
            world_recorder=WorldMemoryRecorder(blocked / "claims.jsonl"),
        )

        self.assertTrue(artifact_path.is_file())
        self.assertIn("completed", json.loads(artifact_path.read_text(encoding="utf-8"))["outcome"])


class ObstacleScenarioWiringTests(unittest.TestCase):
    """A 30-scan run remembers the wall once, from its freshest scan."""

    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "world" / "claims.jsonl"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_only_the_last_scan_of_a_run_is_remembered(self) -> None:
        from simulation.perception.obstacle_scenario import SCENARIO_GRID, SCENARIO_POSE, remember_scanned_world

        documents = [_scan_document(NOW + timedelta(seconds=index)) for index in range(3)]

        result = remember_scanned_world(
            documents,
            WorldMemoryRecorder(self.path),
            pose=SCENARIO_POSE,
            grid=SCENARIO_GRID,
            now=NOW + timedelta(seconds=3),
        )

        claims = load_world_memory(self.path).claims
        self.assertTrue(result.complete)
        self.assertEqual(len(claims), 2, "one obstacle sector and one map cell, from one scan")
        self.assertEqual(
            {claim.observed_at for claim in claims},
            {NOW + timedelta(seconds=2)},
            "the freshest scan speaks for the run",
        )

    def test_a_run_that_captured_nothing_remembers_nothing(self) -> None:
        from simulation.perception.obstacle_scenario import SCENARIO_GRID, SCENARIO_POSE, remember_scanned_world

        result = remember_scanned_world(
            [], WorldMemoryRecorder(self.path), pose=SCENARIO_POSE, grid=SCENARIO_GRID, now=NOW
        )

        self.assertEqual(result.written, 0)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
