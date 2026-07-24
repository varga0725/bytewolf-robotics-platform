"""World memory holds evidence, not beliefs, and never holds a person.

Every claim must carry source, observation time, confidence and expiry. What
the store returns is what may be believed *now*: expired evidence is gone and
a contradicted subject yields nothing until the evidence agrees again.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.memory.world_memory import (
    WorldMemory,
    WorldMemoryError,
    append_claim,
    load_world_claim,
    load_world_memory,
    world_claim_categories,
)


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


def claim_document(
    *,
    subject: str = "marker:red-pad",
    category: str = "landmark",
    statement: str = "A piros leszállójel a kiindulóponttól 5 m-re északra van.",
    source: str = "camera:down_rgb",
    observed_at: datetime = NOW,
    ttl_s: float = 600,
    confidence: float = 0.9,
    **extra: object,
) -> dict[str, object]:
    evidence: dict[str, object] = {
        "source": source,
        "observed_at": observed_at.isoformat(),
        "expires_at": (observed_at + timedelta(seconds=ttl_s)).isoformat(),
        "confidence": confidence,
    }
    evidence.update(extra)
    return {
        "contract_version": "v0.1",
        "subject": subject,
        "category": category,
        "statement": statement,
        "evidence": evidence,
    }


class WorldClaimContractTests(unittest.TestCase):
    def test_a_fully_evidenced_claim_is_admitted(self) -> None:
        claim = load_world_claim(claim_document(artifact="simulation/artifacts/perception/run.json"))

        self.assertEqual(claim.subject, "marker:red-pad")
        self.assertEqual(claim.source, "camera:down_rgb")
        self.assertEqual(claim.confidence, 0.9)
        self.assertEqual(claim.artifact, "simulation/artifacts/perception/run.json")
        self.assertGreater(claim.expires_at, claim.observed_at)

    def test_each_missing_evidence_field_refuses_the_claim(self) -> None:
        for field in ("source", "observed_at", "expires_at", "confidence"):
            document = claim_document()
            del document["evidence"][field]  # type: ignore[union-attr]
            with self.subTest(field=field), self.assertRaises(WorldMemoryError):
                load_world_claim(document)

    def test_a_claim_that_expires_before_it_was_observed_is_refused(self) -> None:
        with self.assertRaisesRegex(WorldMemoryError, "expire after"):
            load_world_claim(claim_document(ttl_s=-1))

    def test_zero_confidence_is_not_evidence(self) -> None:
        with self.assertRaises(WorldMemoryError):
            load_world_claim(claim_document(confidence=0))

    def test_a_naive_timestamp_cannot_be_aged(self) -> None:
        document = claim_document()
        document["evidence"]["observed_at"] = "2026-07-20T12:00:00"  # type: ignore[index]
        with self.assertRaisesRegex(WorldMemoryError, "offset"):
            load_world_claim(document)

    def test_identity_and_biometric_claims_are_out_of_scope(self) -> None:
        for statement in (
            "A kamera arcfelismeréssel azonosította a személyt.",
            "Face recognition matched the operator.",
            "Biometrikus egyezés a hangárnál.",
        ):
            with self.subTest(statement=statement), self.assertRaisesRegex(WorldMemoryError, "identity"):
                load_world_claim(claim_document(statement=statement))

    def test_personal_memory_categories_are_not_world_categories(self) -> None:
        from apps.gateway.memory_store import ALLOWED_CATEGORIES as PERSONAL_CATEGORIES

        for category in PERSONAL_CATEGORIES:
            with self.subTest(category=category), self.assertRaises(WorldMemoryError):
                load_world_claim(claim_document(category=category))

    def test_the_two_stores_share_no_category(self) -> None:
        from apps.gateway.memory_store import ALLOWED_CATEGORIES as PERSONAL_CATEGORIES

        world_categories = world_claim_categories()

        self.assertEqual(world_categories & set(PERSONAL_CATEGORIES), set())


class WorldMemoryRecallTests(unittest.TestCase):
    def test_expired_evidence_is_not_a_slightly_older_fact(self) -> None:
        memory = WorldMemory([load_world_claim(claim_document(ttl_s=60))])

        self.assertEqual(len(memory.recall(NOW + timedelta(seconds=30))), 1)
        self.assertEqual(memory.recall(NOW + timedelta(seconds=61)), ())
        self.assertEqual(len(memory.claims), 1, "the evidence log itself stays append-only")

    def test_low_confidence_evidence_stays_below_the_caller_s_floor(self) -> None:
        memory = WorldMemory([load_world_claim(claim_document(confidence=0.3))])

        self.assertEqual(memory.recall(NOW), ())
        self.assertEqual(len(memory.recall(NOW, min_confidence=0.2)), 1)

    def test_a_more_confident_later_observation_supersedes_the_earlier_one(self) -> None:
        memory = WorldMemory([
            load_world_claim(claim_document(statement="A jel a padlón van.", confidence=0.6)),
            load_world_claim(
                claim_document(
                    statement="A jel az asztalon van.",
                    confidence=0.9,
                    observed_at=NOW + timedelta(seconds=10),
                )
            ),
        ])

        recalled = memory.recall(NOW + timedelta(seconds=20))

        self.assertEqual([claim.statement for claim in recalled], ["A jel az asztalon van."])
        self.assertEqual(memory.disputed(NOW + timedelta(seconds=20)), ())

    def test_a_weaker_contradiction_leaves_the_subject_without_a_fact(self) -> None:
        memory = WorldMemory([
            load_world_claim(claim_document(statement="A jel a padlón van.", confidence=0.9)),
            load_world_claim(
                claim_document(
                    statement="A jel az asztalon van.",
                    confidence=0.4,
                    observed_at=NOW + timedelta(seconds=10),
                )
            ),
        ])
        moment = NOW + timedelta(seconds=20)

        self.assertEqual(memory.recall(moment, min_confidence=0.3), ())
        self.assertEqual(
            [claim.statement for claim in memory.disputed(moment, min_confidence=0.3)],
            ["A jel a padlón van."],
        )

    def test_a_repeated_observation_settles_an_earlier_dispute(self) -> None:
        memory = WorldMemory([
            load_world_claim(claim_document(statement="A jel a padlón van.", confidence=0.9)),
            load_world_claim(
                claim_document(
                    statement="A jel az asztalon van.",
                    confidence=0.4,
                    observed_at=NOW + timedelta(seconds=10),
                )
            ),
            load_world_claim(
                claim_document(
                    statement="A jel a padlón van.",
                    confidence=0.8,
                    observed_at=NOW + timedelta(seconds=20),
                )
            ),
        ])
        moment = NOW + timedelta(seconds=30)

        self.assertEqual([claim.statement for claim in memory.recall(moment, min_confidence=0.3)], ["A jel a padlón van."])
        self.assertEqual(memory.disputed(moment, min_confidence=0.3), ())

    def test_different_subjects_never_contradict_each_other(self) -> None:
        memory = WorldMemory([
            load_world_claim(claim_document(subject="marker:red-pad", statement="A jel a padlón van.")),
            load_world_claim(claim_document(subject="obstacle:north-wall", category="obstacle", statement="Fal 4 m-re északra.")),
        ])

        self.assertEqual(len(memory.recall(NOW)), 2)


class WorldMemoryLogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = TemporaryDirectory()
        self.path = Path(self.directory.name) / "world" / "claims.jsonl"

    def tearDown(self) -> None:
        self.directory.cleanup()

    def test_a_recorded_claim_round_trips_with_its_evidence(self) -> None:
        append_claim(self.path, load_world_claim(claim_document(artifact="simulation/artifacts/perception/run.json")))

        restored = load_world_memory(self.path).recall(NOW)

        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].source, "camera:down_rgb")
        self.assertEqual(restored[0].artifact, "simulation/artifacts/perception/run.json")

    def test_a_corrupt_line_does_not_make_the_world_unreadable(self) -> None:
        append_claim(self.path, load_world_claim(claim_document()))
        with self.path.open("a", encoding="utf-8") as log:
            log.write("{not json\n")
            log.write('{"contract_version": "v0.1", "subject": "x"}\n')
        append_claim(self.path, load_world_claim(claim_document(subject="obstacle:north-wall", category="obstacle")))

        self.assertEqual(len(load_world_memory(self.path).recall(NOW)), 2)

    def test_a_missing_log_reads_as_an_empty_world(self) -> None:
        self.assertEqual(load_world_memory(self.path).recall(NOW), ())


class WorldMemoryApiTests(unittest.TestCase):
    """The dashboard reads world memory; it has no way to write it."""

    def setUp(self) -> None:
        from fastapi.testclient import TestClient

        from apps.api.command_gateway import AgentReply, DashboardCommandGateway
        from apps.api.server import create_app

        self.directory = TemporaryDirectory()
        root = Path(self.directory.name)
        self.path = root / "world" / "claims.jsonl"
        gateway = DashboardCommandGateway(
            converse=lambda _session, _text: AgentReply("Szia!", False, "skipped"),
            review=lambda _text: "plan-1",
            execute=lambda _plan: "submitted",
        )
        self.client = TestClient(
            create_app(root / "telemetry.json", world_memory_path=self.path, gateway=gateway)
        )

    def tearDown(self) -> None:
        self.directory.cleanup()

    def _record(self, **overrides: object) -> None:
        append_claim(self.path, load_world_claim(claim_document(**overrides)))

    def test_live_evidence_travels_with_every_claim(self) -> None:
        self._record(observed_at=datetime.now(UTC), ttl_s=3_600)

        body = self.client.get("/api/v1/world-memory").json()

        self.assertEqual(len(body["claims"]), 1)
        self.assertEqual(body["disputed"], [])
        evidence = body["claims"][0]["evidence"]
        self.assertEqual(set(evidence), {"source", "observed_at", "expires_at", "confidence"})

    def test_expired_evidence_is_not_served_as_a_fact(self) -> None:
        self._record(observed_at=datetime.now(UTC) - timedelta(hours=2), ttl_s=60)

        self.assertEqual(self.client.get("/api/v1/world-memory").json()["claims"], [])

    def test_a_contradicted_subject_is_reported_as_disputed_not_as_a_fact(self) -> None:
        moment = datetime.now(UTC)
        self._record(statement="A jel a padlón van.", confidence=0.9, observed_at=moment, ttl_s=3_600)
        self._record(
            statement="A jel az asztalon van.",
            confidence=0.6,
            observed_at=moment + timedelta(seconds=5),
            ttl_s=3_600,
        )

        body = self.client.get("/api/v1/world-memory").json()

        self.assertEqual(body["claims"], [])
        self.assertEqual([item["statement"] for item in body["disputed"]], ["A jel a padlón van."])

    def test_world_memory_has_no_write_endpoint(self) -> None:
        for method in (self.client.post, self.client.put, self.client.delete):
            with self.subTest(method=method.__name__):
                self.assertEqual(method("/api/v1/world-memory").status_code, 405)


class SchemaCompilationTests(unittest.TestCase):
    """Reading a log must not cost one schema compilation per line.

    `jsonschema.validate` re-derives and re-checks the schema every call. At one
    claim per call that is invisible; at five thousand claims — one survey's
    worth — it took eleven seconds to read the log, which made the dashboard's
    world map endpoint answer in over a minute and its obstacle cells never
    arrive at all. The contract enforced is unchanged; only the compilation is
    shared.
    """

    def test_the_contract_is_compiled_once_and_reused(self) -> None:
        from brain.memory import world_memory

        world_memory._validator.cache_clear()
        first = world_memory._validator()
        second = world_memory._validator()

        self.assertIs(first, second)

    def test_validation_no_longer_goes_through_the_recompiling_entry_point(self) -> None:
        """`jsonschema.validate` is the call that re-derives the schema each time.

        Asserting it is not used is what keeps the cost from creeping back: the
        symptom — a world map endpoint taking a minute — appears only with
        thousands of claims, long after the change that caused it.
        """
        import jsonschema
        from unittest import mock

        from brain.memory import world_memory

        with mock.patch.object(jsonschema, "validate", side_effect=AssertionError("recompiled")):
            for _ in range(50):
                world_memory.validate_world_claim_document(_claim_document())

    def test_the_cached_validator_still_refuses_a_broken_claim(self) -> None:
        from brain.memory import world_memory

        document = _claim_document()
        del document["evidence"]["confidence"]

        with self.assertRaises(WorldMemoryError):
            world_memory.validate_world_claim_document(document)


def _claim_document() -> dict:
    return {
        "contract_version": "v0.1",
        "subject": "obstacle:north-wall",
        "category": "obstacle",
        "statement": "Fal észak felé.",
        "evidence": {
            "source": "gz lidar_2d",
            "observed_at": NOW.isoformat(),
            "expires_at": (NOW + timedelta(minutes=5)).isoformat(),
            "confidence": 0.9,
        },
    }


if __name__ == "__main__":
    unittest.main()
