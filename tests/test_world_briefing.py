"""What Pi is told about the world, and what it can never be told.

The briefing is the only channel from world memory into the conversation. It
must carry the resolver's caution with it: no expired claim, no disputed claim
stated as fact, no unbounded text, and no ability to read anything the
dashboard would not have shown a human.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest

from apps.gateway.pi_agent import PiAgentClient
from brain.memory.briefing import world_briefing
from brain.memory.world_memory import WorldMemory, load_world_claim


NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)
SESSION = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3"


def _claim(
    subject: str = "marker:red-pad",
    *,
    statement: str = "A piros jel a padlón van.",
    confidence: float = 0.9,
    observed_at: datetime = NOW,
    ttl_s: float = 600,
):
    return load_world_claim({
        "contract_version": "v0.1",
        "subject": subject,
        "category": "landmark",
        "statement": statement,
        "evidence": {
            "source": "camera:down_rgb",
            "observed_at": observed_at.isoformat(),
            "expires_at": (observed_at + timedelta(seconds=ttl_s)).isoformat(),
            "confidence": confidence,
        },
    })


class BriefingContentTests(unittest.TestCase):
    def test_every_line_carries_its_source_confidence_and_age(self) -> None:
        text = world_briefing([_claim()], now=NOW + timedelta(seconds=30))

        self.assertIn("camera:down_rgb", text)
        self.assertIn("90%", text)
        self.assertIn("30 másodperce", text)

    def test_an_empty_world_says_so_rather_than_staying_silent(self) -> None:
        self.assertIn("Nincs érvényes", world_briefing([], now=NOW))

    def test_a_disputed_claim_is_marked_uncertain(self) -> None:
        text = world_briefing([], [_claim()], now=NOW)

        self.assertIn("BIZONYTALAN", text)

    def test_expired_evidence_never_reaches_the_briefing(self) -> None:
        memory = WorldMemory([_claim(ttl_s=60)])
        moment = NOW + timedelta(seconds=61)

        text = world_briefing(memory.recall(moment), memory.disputed(moment), now=moment)

        self.assertIn("Nincs érvényes", text)

    def test_the_freshest_evidence_survives_the_budget(self) -> None:
        claims = [
            _claim(f"marker:{index}", statement=f"Jel {index}.", observed_at=NOW + timedelta(seconds=index))
            for index in range(12)
        ]

        text = world_briefing(claims, now=NOW + timedelta(seconds=20), max_claims=3)

        self.assertIn("Jel 11.", text)
        self.assertNotIn("Jel 0.", text)

    def test_the_briefing_is_bounded_and_says_when_it_truncates(self) -> None:
        claims = [
            _claim(f"marker:{index}", statement=f"Egy elég hosszú megfigyelés {index}. sorszámmal.")
            for index in range(8)
        ]

        text = world_briefing(claims, now=NOW, max_chars=200)

        self.assertLessEqual(len(text), 260)
        self.assertIn("hosszkorlát", text)


class BriefingBoundaryTests(unittest.TestCase):
    """Pi receives the briefing as text; it never gains a way to read the store."""

    def test_the_briefing_reaches_the_runner_as_bounded_request_data(self) -> None:
        seen: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: seen.append(request) or {
                "text": "Rendben.", "requests_drone_action": False, "memory_update": "skipped"
            }
        )

        client.converse(SESSION, "mit láttál?", world_briefing([_claim()], now=NOW))

        self.assertIn("world_context", seen[0])
        self.assertIn("A piros jel a padlón van.", str(seen[0]["world_context"]))
        self.assertIn("bizonyosság", str(seen[0]["world_context"]))

    def test_an_oversized_briefing_is_truncated_before_it_reaches_pi(self) -> None:
        seen: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: seen.append(request) or {
                "text": "Rendben.", "requests_drone_action": False, "memory_update": "skipped"
            }
        )

        client.converse(SESSION, "mit láttál?", "x" * 5_000)

        self.assertLessEqual(len(str(seen[0]["world_context"])), 1_200)

    def test_a_turn_without_a_briefing_carries_no_world_field_at_all(self) -> None:
        seen: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: seen.append(request) or {
                "text": "Szia!", "requests_drone_action": False, "memory_update": "skipped"
            }
        )

        client.converse(SESSION, "szia")

        self.assertNotIn("world_context", seen[0])


if __name__ == "__main__":
    unittest.main()
