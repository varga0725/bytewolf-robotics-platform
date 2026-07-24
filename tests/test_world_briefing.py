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


class MissionFeedbackLoopTests(unittest.TestCase):
    """What the agent asked for, it can learn the outcome of.

    Pi requests a plan, a human approves it, and the executor runs it in a
    separate process the agent never sees. Before mission outcomes were
    recorded, that was the end of the story: the agent could ask for a flight
    and never find out whether it happened. The audit artifact closes the loop
    by becoming a claim the next turn's briefing carries.
    """

    def test_a_finished_run_becomes_something_the_agent_can_say_next_turn(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from brain.cli.artifacts import write_run_artifact
        from brain.memory.recorder import WorldMemoryRecorder
        from brain.memory.world_memory import load_world_memory
        from brain.mission.execution import MissionExecution

        with TemporaryDirectory() as directory:
            root = Path(directory)
            world_path = root / "world" / "claims.jsonl"

            write_run_artifact(
                root / "runs",
                MissionExecution.empty(),
                "approved",
                "completed",
                None,
                world_recorder=WorldMemoryRecorder(world_path),
            )

            memory = load_world_memory(world_path)
            now = datetime.now(UTC)
            briefing = world_briefing(memory.recall(now), memory.disputed(now), now=now)

        self.assertIn("mission_outcome", briefing)
        self.assertIn("completed", briefing)

    def test_a_failed_run_is_reported_as_such_not_omitted(self) -> None:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from brain.cli.artifacts import write_run_artifact
        from brain.memory.recorder import WorldMemoryRecorder
        from brain.memory.world_memory import load_world_memory
        from brain.mission.execution import MissionExecution

        with TemporaryDirectory() as directory:
            root = Path(directory)
            world_path = root / "world" / "claims.jsonl"

            write_run_artifact(
                root / "runs",
                MissionExecution.empty(),
                "approved",
                "failed",
                "MissionPreflightError: battery below reserve",
                world_recorder=WorldMemoryRecorder(world_path),
            )

            memory = load_world_memory(world_path)
            now = datetime.now(UTC)
            briefing = world_briefing(memory.recall(now), memory.disputed(now), now=now)

        self.assertIn("failed", briefing)
        self.assertIn("battery below reserve", briefing)


class CapabilityBriefingTests(unittest.TestCase):
    """The agent's self-model comes from the same file the gate enforces."""

    def _profile(self):
        from brain.safety.profile import load_safety_profile

        return load_safety_profile()

    def test_the_envelope_is_stated_in_the_units_the_gate_uses(self) -> None:
        from brain.memory.briefing import capability_briefing

        profile = self._profile()
        text = capability_briefing(profile)

        self.assertIn(f"{profile.max_altitude_m:g} m", text)
        self.assertIn(f"{profile.max_speed_m_s:g} m/s", text)
        self.assertIn(f"{profile.max_radius_m:g} m", text)
        self.assertIn(f"{profile.minimum_battery_percent_to_start:g}%", text)

    def test_the_briefing_frames_limits_as_refusals_not_permissions(self) -> None:
        from brain.memory.briefing import capability_briefing

        text = capability_briefing(self._profile())

        self.assertIn("ne ígérd meg", text)
        self.assertIn("gate", text)

    def test_it_never_becomes_a_second_source_of_a_limit(self) -> None:
        """Every number must come from the profile object, not from the text."""
        from dataclasses import replace

        from brain.memory.briefing import capability_briefing

        tightened = replace(self._profile(), max_altitude_m=7.5)

        self.assertIn("7.5 m", capability_briefing(tightened))

    def test_the_envelope_reaches_pi_as_its_own_bounded_field(self) -> None:
        from brain.memory.briefing import capability_briefing

        seen: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: seen.append(request) or {
                "text": "Rendben.", "requests_drone_action": False, "memory_update": "skipped"
            }
        )

        client.converse(SESSION, "milyen magasra tudsz menni?", "", capability_briefing(self._profile()))

        self.assertIn("capability_context", seen[0])
        self.assertNotIn("world_context", seen[0], "an empty world is not sent as an empty section")


if __name__ == "__main__":
    unittest.main()
