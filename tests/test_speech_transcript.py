"""Coverage for the Speech Transcript v0.1 contract.

Speech is an input, never an authority, so most of these tests are about what a
transcript cannot say: it cannot carry a structured command, it cannot declare
itself pre-approved, and a partial transcript cannot suggest anything at all --
acting on half an utterance is how "ne szállj fel" becomes "szállj fel". The
rest cover freshness, because a spoken request that has aged out refers to a
situation that may be gone.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

from brain.speech.transcript import (
    SPEECH_CONTRACT_VERSION,
    TranscriptContractError,
    TranscriptState,
    load_transcript,
)


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared/interfaces/speech/examples"
SPEECH_PKG = ROOT / "brain/speech"
NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _transcript(**overrides) -> dict:
    return {**_read(EXAMPLES / "valid/transcript_with_suggestion.v0_1.json"), **overrides}


class FixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self) -> None:
        fixtures = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(fixtures)
        for path in fixtures:
            with self.subTest(fixture=path.name):
                load_transcript(_read(path))

    def test_every_invalid_fixture_is_rejected(self) -> None:
        fixtures = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(fixtures, "refusal is the point; there must be invalid fixtures")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                with self.assertRaises(TranscriptContractError):
                    load_transcript(_read(path))


class SafetyBoundaryTests(unittest.TestCase):
    """What speech is structurally unable to do."""

    def test_a_suggestion_cannot_declare_itself_pre_approved(self) -> None:
        document = _transcript()
        document["command_suggestion"]["requires_approval"] = False
        with self.assertRaises(TranscriptContractError):
            load_transcript(document)

    def test_a_suggestion_cannot_carry_a_structured_command(self) -> None:
        document = _transcript()
        document["command_suggestion"]["mission_spec"] = {"steps": []}
        with self.assertRaises(TranscriptContractError):
            load_transcript(document)

    def test_a_partial_transcript_cannot_suggest_anything(self) -> None:
        document = _transcript(is_final=False)
        with self.assertRaises(TranscriptContractError):
            load_transcript(document)

    def test_a_loaded_suggestion_always_requires_approval(self) -> None:
        transcript = load_transcript(_transcript())
        self.assertIsNotNone(transcript.command_suggestion)
        self.assertTrue(transcript.command_suggestion.requires_approval)

    def test_the_speech_domain_never_imports_flight_control(self) -> None:
        for source in SPEECH_PKG.rglob("*.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "mavlink", "px4", "pymavlink"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)


class FreshnessTests(unittest.TestCase):
    def test_a_fresh_utterance_is_valid(self) -> None:
        transcript = load_transcript(_transcript())
        self.assertEqual(transcript.state(NOW + timedelta(seconds=5)), TranscriptState.VALID)
        self.assertTrue(transcript.state(NOW).usable)

    def test_an_aged_out_utterance_is_stale(self) -> None:
        transcript = load_transcript(_transcript(max_age_s=5.0))
        self.assertEqual(transcript.state(NOW + timedelta(seconds=30)), TranscriptState.STALE)

    def test_producer_declared_invalid_and_missing_are_kept(self) -> None:
        # A recogniser that does not trust what it heard, or heard nothing at
        # all, says so; neither carries a suggestion.
        for validity, expected in (("invalid", TranscriptState.INVALID), ("missing", TranscriptState.MISSING)):
            with self.subTest(validity=validity):
                document = _transcript(validity=validity, text="")
                document.pop("command_suggestion")
                self.assertEqual(load_transcript(document).state(NOW), expected)

    def test_a_far_future_utterance_is_invalid_not_brand_new(self) -> None:
        transcript = load_transcript(_transcript())
        self.assertEqual(transcript.state(NOW - timedelta(hours=1)), TranscriptState.INVALID)

    def test_an_infinite_freshness_window_is_refused(self) -> None:
        infinite = json.loads('{"v": 1e400}')["v"]
        with self.assertRaises(TranscriptContractError):
            load_transcript(_transcript(max_age_s=infinite))

    def test_a_naive_now_is_refused(self) -> None:
        transcript = load_transcript(_transcript())
        with self.assertRaises(TranscriptContractError):
            transcript.state(datetime(2026, 7, 25, 12, 0, 0))


class ContractTests(unittest.TestCase):
    def test_a_future_contract_version_is_rejected(self) -> None:
        with self.assertRaises(TranscriptContractError):
            load_transcript(_transcript(contract_version="v0.2"))

    def test_the_engine_and_language_are_recorded(self) -> None:
        transcript = load_transcript(_transcript())
        self.assertEqual(transcript.engine["id"], "nvidia/parakeet-tdt-0.6b-v3")
        self.assertEqual(transcript.language, "hu-HU")
        self.assertEqual(SPEECH_CONTRACT_VERSION, "v0.1")

    def test_raw_audio_cannot_enter_the_contract(self) -> None:
        with self.assertRaises(TranscriptContractError):
            load_transcript(_transcript(audio_base64="AAAA"))


if __name__ == "__main__":
    unittest.main()
