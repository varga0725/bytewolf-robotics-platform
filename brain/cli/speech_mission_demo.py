"""Walk one spoken request through the whole chain, up to a flight.

```
speech ──> transcript_v0_1 ──> command_suggestion
                                     │  (gate 1: a human asks for review)
                                     v
                          NIM review ──> MissionSpec + SafetyGate
                                     │  (gate 2: a human approves the exact plan)
                                     v
                                  PX4 SITL
```

The point of this CLI is the two gates, not the convenience. Speech reaches the
review step only because a person asked for it with ``--review``, and the plan
reaches PX4 only because a person approved that exact file with ``--execute``.
Without those flags it stops and prints what it would have done, which is also
how it stays safe to run while a simulator is up.

Nothing here shortens the existing path: review and execution are the same
``fly_nim_mission`` steps the dashboard and the Telegram gateway use, so speech
is one more way to *ask*, never a second way to fly.

Example::

    python -m brain.cli.speech_mission_demo --text "Szállj fel két méterre és gyere vissza."
    python -m brain.cli.speech_mission_demo --audio utterance.wav --review
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path
import subprocess
import sys
import uuid

from brain.speech.adapters.faster_whisper_stt import FasterWhisperStt
from brain.speech.adapters.stt import RawTranscription
from brain.speech.recognition import SpeechRecogniser
from brain.speech.transcript import Transcript, TranscriptState


class _TextEngine:
    """Stand in for a recogniser when the words are already known.

    Typing the sentence makes the rest of the chain reproducible: the same
    request every run, with no microphone and no model in the way.
    """

    engine_id = "typed-text"
    engine_version = "v1"

    def __init__(self, text: str, confidence: float = 0.95) -> None:
        self._text = text
        self._confidence = confidence

    def transcribe(self, audio: bytes, *, language: str) -> RawTranscription:
        return RawTranscription(text=self._text, is_final=True, confidence=self._confidence, language=language)


def _recognise(arguments: argparse.Namespace) -> Transcript:
    if arguments.text:
        engine = _TextEngine(arguments.text)
        audio = b"typed"
        source = "typed:demo"
    else:
        engine = FasterWhisperStt(model_size=arguments.model)
        audio = Path(arguments.audio).read_bytes()
        source = f"file:{Path(arguments.audio).name}"
    recogniser = SpeechRecogniser(engine, source=source, language=arguments.language)
    return recogniser.recognise(audio, transcript_id=f"utt-{uuid.uuid4().hex[:8]}")


def _review(intent_text: str, timeout_s: float) -> subprocess.CompletedProcess:
    """Hand the spoken request to the same review step every other caller uses."""
    return subprocess.run(
        [sys.executable, "-m", "brain.cli.fly_nim_mission", "--command", intent_text],
        capture_output=True, text=True, timeout=timeout_s, check=False,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text", help="Skip recognition and use these words, for a reproducible run.")
    source.add_argument("--audio", help="A recorded utterance to transcribe.")
    parser.add_argument("--model", default="large-v3-turbo", help="Local Whisper model size.")
    parser.add_argument("--language", default="hu-HU")
    parser.add_argument("--review-timeout-s", type=float, default=90.0)
    parser.add_argument(
        "--review", action="store_true",
        help="Gate 1: actually send the spoken request to mission review. Without it, nothing is planned.",
    )
    arguments = parser.parse_args(argv)

    print("1) speech → transcript")
    transcript = _recognise(arguments)
    state = transcript.state(datetime.now(UTC))
    print(f"   text:  {transcript.text!r}")
    print(f"   state: {state.value}  confidence: {transcript.confidence}  engine: {transcript.engine['id']}")

    if state is not TranscriptState.VALID:
        print(f"\nstopping: a {state.value} transcript is not something to act on.")
        return 1

    suggestion = transcript.command_suggestion
    print("\n2) transcript → command suggestion")
    if suggestion is None:
        print("   none: too uncertain, or nothing actionable was said.")
        print("   the runtime would ask the user what they meant rather than guess.")
        return 0
    print(f"   intent:   {suggestion.intent_text!r}")
    print(f"   approval: required={suggestion.requires_approval} (the contract pins this to true)")

    if not arguments.review:
        print("\n3) mission review — NOT run")
        print("   speech alone plans nothing. Re-run with --review to ask for a plan.")
        return 0

    print("\n3) command suggestion → mission review (NIM) + SafetyGate")
    try:
        completed = _review(suggestion.intent_text, arguments.review_timeout_s)
    except subprocess.TimeoutExpired:
        print("   review timed out; nothing was planned and the drone received no command.")
        return 2
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip().splitlines()[-1:] or ["no detail"]
        print(f"   review refused the request: {detail[0]}")
        print("   this is the SafetyGate doing its job; nothing was planned.")
        return 3
    print("   " + "\n   ".join(output.splitlines()[-8:]))

    print("\n4) execution — NOT run by this CLI")
    print("   the plan above needs an explicit human approval of that exact file.")
    print("   run it yourself: python -m brain.cli.fly_nim_mission --mission-spec-file <path> --execute")
    return 0


if __name__ == "__main__":
    sys.exit(main())
