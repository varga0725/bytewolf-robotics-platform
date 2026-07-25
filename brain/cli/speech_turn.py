"""Run one push-to-talk speech turn end to end, locally.

Records a bounded utterance, transcribes it, publishes it as a validated
transcript, and optionally speaks a reply. It is the smallest thing that
exercises the whole speech path on real engines.

What it deliberately does not do is fly anything. A recognised request becomes a
``command_suggestion`` carrying ``requires_approval``; turning that into a
mission is the Cognitive Runtime's job, through reviewed MissionSpec, the
SafetyGate and an explicit approval. This CLI has no path to an executor.

Example::

    python -m brain.cli.speech_turn --seconds 4 --model large-v3-turbo --speak-back
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import sys
import uuid

from brain.speech.adapters.faster_whisper_stt import FasterWhisperStt
from brain.speech.adapters.macos_tts import MacSayTts
from brain.speech.adapters.microphone import FfmpegMicrophone, MicrophoneError, input_devices
from brain.speech.adapters.tts import TtsError
from brain.speech.recognition import SpeechRecogniser
from brain.speech.session import PushToTalkSession


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seconds", type=float, default=4.0, help="How long to record.")
    parser.add_argument("--device", default="0", help="AVFoundation audio input index.")
    parser.add_argument("--model", default="large-v3-turbo", help="Local Whisper model size.")
    parser.add_argument("--language", default="hu-HU")
    parser.add_argument("--transcript", type=Path, default=None, help="Write the transcript document here.")
    parser.add_argument("--speak-back", action="store_true", help="Read the recognised text back aloud.")
    parser.add_argument("--list-devices", action="store_true", help="List audio inputs and exit.")
    arguments = parser.parse_args(argv)

    if arguments.list_devices:
        for index, name in input_devices():
            print(f"[{index}] {name}")
        return 0

    session = PushToTalkSession()
    microphone = FfmpegMicrophone(device=arguments.device)

    session.press()
    print(f"recording {arguments.seconds:g}s from input {arguments.device} — speak now…", flush=True)
    try:
        audio = microphone.record(arguments.seconds)
    except MicrophoneError as error:
        print(f"capture failed: {error}")
        return 2
    session.feed(audio)
    utterance = session.release()
    if not utterance:
        print("nothing was captured")
        return 1

    print(f"transcribing {len(utterance)} bytes with whisper-{arguments.model}…", flush=True)
    recogniser = SpeechRecogniser(
        FasterWhisperStt(model_size=arguments.model),
        source=f"mic:avfoundation-{arguments.device}",
        language=arguments.language,
    )
    transcript = recogniser.recognise(utterance, transcript_id=f"utt-{uuid.uuid4().hex[:8]}")
    session.transcribed()

    state = transcript.state(datetime.now(UTC))
    print(f"\ntranscript: {transcript.text!r}")
    print(f"  state={state.value} confidence={transcript.confidence} language={transcript.language}")
    suggestion = transcript.command_suggestion
    if suggestion is None:
        print("  no command suggestion: too uncertain, or nothing actionable was said")
    else:
        print(f"  suggestion: {suggestion.intent_text!r} (requires approval: {suggestion.requires_approval})")

    if arguments.transcript is not None:
        document = {
            "transcript_id": transcript.transcript_id,
            "text": transcript.text,
            "language": transcript.language,
            "confidence": transcript.confidence,
            "state": state.value,
            "engine": transcript.engine,
            "command_suggestion": (
                {"intent_text": suggestion.intent_text, "requires_approval": True}
                if suggestion else None
            ),
        }
        arguments.transcript.parent.mkdir(parents=True, exist_ok=True)
        arguments.transcript.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"  written: {arguments.transcript}")

    if arguments.speak_back and transcript.text:
        session.speak()
        try:
            spoken = MacSayTts().speak(f"Ezt értettem: {transcript.text}", language=arguments.language)
        except TtsError as error:
            print(f"could not speak back: {error}")
        else:
            _play(spoken.audio)
    session.finish()
    return 0


def _play(audio: bytes) -> None:
    """Play synthesised audio, best effort: a silent host is not an error."""
    import shutil
    import subprocess
    import tempfile

    player = shutil.which("afplay")
    if player is None:
        return
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as handle:
        handle.write(audio)
        handle.flush()
        subprocess.run([player, handle.name], capture_output=True, timeout=60, check=False)


if __name__ == "__main__":
    sys.exit(main())
