"""Coverage for the cognitive-hooks proposal contract, admission and runtime.

The pipeline's job is to let a background model suggest durable memory without
granting it a write: most of these tests are about refusal -- a sensitive value,
a duplicate, an over-budget batch, a malformed document -- and about the store
only ever changing through admission.
"""

import json
from pathlib import Path
import unittest

from brain.cognitive_hooks import (
    HookRuntime,
    MAX_OPERATIONS,
    ProposalContractError,
    admit,
    load_proposal,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared/interfaces/cognitive_hooks/examples"


def _proposal(operations, *, proposal_id="p", version="v0.1", created="2026-07-23T09:00:00+00:00"):
    return {
        "contract_version": version,
        "proposal_id": proposal_id,
        "kind": "memory_delta",
        "source": {"job": "post_turn_memory", "model": "m", "prompt_version": "v0_2"},
        "created_at": created,
        "payload": {"operations": operations},
    }


def _op(value, *, op="upsert", category="preference"):
    return {"op": op, "category": category, "value": value}


class FixtureTests(unittest.TestCase):
    def test_valid_fixtures_load_and_invalid_are_refused(self) -> None:
        for path in sorted((EXAMPLES / "valid").glob("*.json")):
            with self.subTest(fixture=path.name):
                load_proposal(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted((EXAMPLES / "invalid").glob("*.json")):
            with self.subTest(fixture=path.name):
                with self.assertRaises(ProposalContractError):
                    load_proposal(json.loads(path.read_text(encoding="utf-8")))


class AdmissionTests(unittest.TestCase):
    def test_sensitive_values_are_rejected_even_in_a_valid_category(self) -> None:
        cases = {
            "e-mail": "írj a felhasznalo@example.com címre",
            "secret": "a jelszó titkos123",
            "phone": "hívd a +36 30 123 4567 számot",
            "address": "a Kossuth utca 12 alatt lakik",
        }
        for label, value in cases.items():
            with self.subTest(case=label):
                result = admit(load_proposal(_proposal([_op(value)])))
                self.assertEqual(result.outcome, "skipped")
                self.assertEqual(len(result.rejected), 1)

    def test_over_budget_batch_stores_nothing(self) -> None:
        ops = [_op(f"tény {i}") for i in range(MAX_OPERATIONS + 1)]
        result = admit(load_proposal(_proposal(ops)))
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.accepted, ())

    def test_duplicate_is_dropped_against_known_values(self) -> None:
        proposal = load_proposal(_proposal([_op("A Baylands világ")]))
        result = admit(proposal, known=frozenset({"a baylands világ"}))
        self.assertEqual(result.outcome, "skipped")
        self.assertEqual(result.rejected[0]["reason"], "duplicate")

    def test_forget_is_ordered_before_upsert(self) -> None:
        result = admit(load_proposal(_proposal([
            _op("új kedvenc", op="upsert"),
            _op("régi kedvenc", op="forget"),
        ])))
        self.assertEqual(result.accepted[0]["op"], "forget")


class RuntimeTests(unittest.TestCase):
    def test_happy_path_stores_and_audits(self) -> None:
        runtime = HookRuntime()
        record = runtime.submit("s1", _proposal([_op("A Baylands világ az alap")]))
        self.assertEqual(record.outcome, "updated")
        self.assertEqual(len(runtime.store.facts("s1")), 1)
        self.assertEqual(len(runtime.store.audit("s1")), 1)

    def test_malformed_document_is_unavailable_and_stores_nothing(self) -> None:
        runtime = HookRuntime()
        record = runtime.submit("s1", {"contract_version": "v0.1", "kind": "memory_delta"})
        self.assertEqual(record.outcome, "unavailable")
        self.assertEqual(runtime.store.facts("s1"), ())

    def test_resubmitting_the_same_fact_is_a_no_op(self) -> None:
        runtime = HookRuntime()
        runtime.submit("s1", _proposal([_op("A Baylands világ")], proposal_id="p1"))
        second = runtime.submit("s1", _proposal([_op("a  baylands  világ")], proposal_id="p2"))
        self.assertEqual(second.outcome, "skipped")
        self.assertEqual(len(runtime.store.facts("s1")), 1)

    def test_forget_removes_a_stored_fact(self) -> None:
        runtime = HookRuntime()
        runtime.submit("s1", _proposal([_op("a régi hangár", category="place_label")], proposal_id="p1"))
        runtime.submit("s1", _proposal([_op("a régi hangár", op="forget", category="place_label")], proposal_id="p2"))
        self.assertEqual(runtime.store.facts("s1"), ())


class SafetyTests(unittest.TestCase):
    def test_cognitive_hooks_never_imports_flight_control(self) -> None:
        package = ROOT / "brain/cognitive_hooks"
        for source in package.rglob("*.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


if __name__ == "__main__":
    unittest.main()
