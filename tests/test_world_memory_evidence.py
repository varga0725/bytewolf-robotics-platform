"""Perception and mission evidence become claims only while they are usable.

The converters are the seam where a live reading turns into something the
robot will still say tomorrow. Anything that may not be acted on must not be
remembered either.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from brain.memory.evidence import (
    claim_from_mission_artifact,
    claim_from_target_observation,
    claims_from_obstacle_observation,
)
from brain.memory.world_memory import WorldMemory
from brain.mission.artifacts import MissionAuditArtifact
from brain.mission.execution import MissionExecution
from brain.perception.target_estimator import GlobalFix, TargetObservation
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def _target(*, validity: str = "valid", confidence: float | None = 0.95, fix: GlobalFix | None = None) -> TargetObservation:
    return TargetObservation(
        captured_at=NOW,
        max_age_s=0.5,
        declared_validity=validity,
        label="landing-pad",
        confidence=confidence,
        offset_north_m=1.5,
        offset_east_m=-2.0,
        range_m=10.0,
        horizontal_uncertainty_m=0.15,
        global_fix=fix,
        source="camera:down_rgb",
    )


def _obstacle_document(sectors: list[dict[str, object]]) -> dict[str, object]:
    return {
        "contract_version": "v0.1",
        "kind": "obstacle",
        "vehicle_id": "x500v2_reference_01",
        "observed_at": NOW.isoformat(),
        "max_age_s": 1.0,
        "validity": "valid",
        "source": "gz lidar_2d_v2",
        "payload": {
            "frame": "body_frd",
            "sensor": {"id": "lidar_2d_v2", "min_range_m": 0.1, "max_range_m": 30.0},
            "sectors": sectors,
        },
    }


class TargetSightingClaimTests(unittest.TestCase):
    def test_a_usable_sighting_carries_its_source_confidence_and_expiry(self) -> None:
        claim = claim_from_target_observation(
            _target(), NOW, subject="marker:red-pad", artifact="simulation/artifacts/perception/run.json"
        )

        assert claim is not None
        self.assertEqual(claim.category, "target_sighting")
        self.assertEqual(claim.source, "camera:down_rgb")
        self.assertEqual(claim.confidence, 0.95)
        self.assertEqual(claim.artifact, "simulation/artifacts/perception/run.json")
        self.assertGreater(claim.expires_at, claim.observed_at)
        self.assertIn("landing-pad", claim.statement)

    def test_an_unusable_sighting_is_not_remembered(self) -> None:
        for validity in ("invalid", "missing"):
            with self.subTest(validity=validity):
                self.assertIsNone(
                    claim_from_target_observation(_target(validity=validity), NOW, subject="marker:red-pad")
                )

    def test_a_stale_sighting_is_not_remembered(self) -> None:
        self.assertIsNone(
            claim_from_target_observation(_target(), NOW + timedelta(seconds=5), subject="marker:red-pad")
        )

    def test_a_sighting_without_confidence_is_not_evidence(self) -> None:
        self.assertIsNone(claim_from_target_observation(_target(confidence=None), NOW, subject="marker:red-pad"))

    def test_a_global_fix_travels_with_the_claim(self) -> None:
        claim = claim_from_target_observation(
            _target(fix=GlobalFix(47.397971, 8.546164)), NOW, subject="marker:red-pad"
        )

        assert claim is not None and claim.position is not None
        self.assertEqual(claim.position["frame"], "wgs84")
        self.assertAlmostEqual(claim.position["latitude_deg"], 47.397971)


class ObstacleClaimTests(unittest.TestCase):
    def test_only_measured_sectors_become_claims(self) -> None:
        observation = load_observation(_obstacle_document([
            {"yaw_deg": 0.0, "width_deg": 10.0, "coverage": "measured", "distance_m": 4.2, "confidence": 0.8},
            {"yaw_deg": 45.0, "width_deg": 10.0, "coverage": "clear"},
            {"yaw_deg": 180.0, "width_deg": 90.0, "coverage": "unobserved"},
        ]))

        claims = claims_from_obstacle_observation(observation, NOW)

        self.assertEqual(len(claims), 1)
        self.assertEqual(claims[0].category, "obstacle")
        self.assertEqual(claims[0].confidence, 0.8)
        self.assertEqual(claims[0].vehicle_id, "x500v2_reference_01")
        self.assertIn("4.2 m", claims[0].statement)

    def test_a_blind_sector_never_becomes_remembered_free_space(self) -> None:
        observation = load_observation(_obstacle_document([
            {"yaw_deg": 180.0, "width_deg": 90.0, "coverage": "unobserved"},
        ]))

        self.assertEqual(claims_from_obstacle_observation(observation, NOW), ())

    def test_a_stale_scan_is_not_remembered(self) -> None:
        observation = load_observation(_obstacle_document([
            {"yaw_deg": 0.0, "width_deg": 10.0, "coverage": "measured", "distance_m": 4.2},
        ]))

        self.assertEqual(claims_from_obstacle_observation(observation, NOW + timedelta(seconds=5)), ())

    def test_obstacle_claims_expire_faster_than_they_would_mislead(self) -> None:
        observation = load_observation(_obstacle_document([
            {"yaw_deg": 0.0, "width_deg": 10.0, "coverage": "measured", "distance_m": 4.2},
        ]))
        claims = claims_from_obstacle_observation(observation, NOW, ttl_s=60)

        memory = WorldMemory(claims)

        self.assertEqual(len(memory.recall(NOW + timedelta(seconds=30))), 1)
        self.assertEqual(memory.recall(NOW + timedelta(seconds=61)), ())


class MissionOutcomeClaimTests(unittest.TestCase):
    def test_a_recorded_run_becomes_a_sourced_mission_claim(self) -> None:
        artifact = MissionAuditArtifact.from_execution(
            "3f6c2b41-0d3f-4f0e-8b4a-6c0f3a2d9e77",
            MissionExecution.empty(),
            recorded_at=NOW,
            safety_decision="approved",
            outcome="completed",
        )

        claim = claim_from_mission_artifact(artifact, artifact_path="simulation/artifacts/agent-missions/run.json")

        self.assertEqual(claim.category, "mission_outcome")
        self.assertEqual(claim.source, "mission-artifact")
        self.assertEqual(claim.confidence, 1.0)
        self.assertIn("completed", claim.statement)
        self.assertGreater(claim.expires_at, claim.observed_at)

    def test_even_mission_history_carries_an_expiry(self) -> None:
        artifact = MissionAuditArtifact.from_execution(
            "3f6c2b41-0d3f-4f0e-8b4a-6c0f3a2d9e77", MissionExecution.empty(), recorded_at=NOW
        )

        claim = claim_from_mission_artifact(artifact, ttl_s=3_600)

        self.assertEqual(claim.expires_at, NOW + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
