"""The engines that actually run on the development machine.

The decided primary STT (NIM Parakeet) needs a GPU host for the speech
microservice, so on a dev Mac the working pair is local Whisper plus the system
Hungarian voice. These tests cover the adapters without downloading a model or
requiring a specific host: the Whisper model is injected, and the macOS tests
skip where ``say`` does not exist.
"""

from pathlib import Path
import shutil
import tempfile
import unittest

from brain.speech.adapters.faster_whisper_stt import FasterWhisperStt, _confidence
from brain.speech.adapters.macos_tts import MacSayTts, available_voices
from brain.speech.adapters.stt import SttError
from brain.speech.adapters.tts import TtsError


class _Segment:
    def __init__(self, text: str, avg_logprob: float | None = -0.3) -> None:
        self.text = text
        self.avg_logprob = avg_logprob


class _Info:
    language = "hu"
    duration = 2.5


class _FakeWhisper:
    def __init__(self, segments, info=None, fail=False):
        self._segments = segments
        self._info = info or _Info()
        self._fail = fail
        self.calls: list[dict] = []

    def transcribe(self, path, language=None):
        self.calls.append({"path": path, "language": language})
        if self._fail:
            raise RuntimeError("model exploded")
        return iter(self._segments), self._info


class FasterWhisperTests(unittest.TestCase):
    def test_segments_are_joined_into_one_transcript(self) -> None:
        model = _FakeWhisper([_Segment(" Mennyi az akku"), _Segment(" töltöttsége? ")])
        engine = FasterWhisperStt(model=model)

        result = engine.transcribe(b"RIFF-audio", language="hu-HU")

        self.assertEqual(result.text, "Mennyi az akku töltöttsége?")
        self.assertEqual(result.language, "hu")
        self.assertEqual(result.duration_s, 2.5)
        # Whisper takes a bare language code, not the BCP-47 tag.
        self.assertEqual(model.calls[0]["language"], "hu")

    def test_the_engine_receives_a_readable_audio_file(self) -> None:
        model = _FakeWhisper([_Segment("szia")])
        FasterWhisperStt(model=model).transcribe(b"RIFF-audio", language="hu-HU")
        # The temporary file is gone afterwards, but the engine saw a real path.
        self.assertTrue(model.calls[0]["path"].endswith(".wav"))

    def test_no_recognised_words_is_an_empty_transcript_not_an_error(self) -> None:
        # The microphone worked; there were simply no words. Upstream turns that
        # into a 'missing' utterance rather than a failure.
        result = FasterWhisperStt(model=_FakeWhisper([])).transcribe(b"RIFF", language="hu-HU")
        self.assertEqual(result.text, "")
        self.assertIsNone(result.confidence)

    def test_an_engine_fault_is_a_refusal(self) -> None:
        with self.assertRaises(SttError):
            FasterWhisperStt(model=_FakeWhisper([], fail=True)).transcribe(b"RIFF", language="hu-HU")

    def test_empty_audio_never_reaches_the_model(self) -> None:
        model = _FakeWhisper([_Segment("hallucination")])
        with self.assertRaises(SttError):
            FasterWhisperStt(model=model).transcribe(b"", language="hu-HU")
        self.assertEqual(model.calls, [])

    def test_confidence_is_bounded_and_absent_when_unreported(self) -> None:
        self.assertIsNone(_confidence([_Segment("a", avg_logprob=None)]))
        value = _confidence([_Segment("a", avg_logprob=-0.2), _Segment("b", avg_logprob=-0.4)])
        self.assertIsNotNone(value)
        self.assertGreaterEqual(value, 0.0)
        self.assertLessEqual(value, 1.0)


@unittest.skipUnless(shutil.which("say"), "the macOS 'say' command is required")
class MacSayTests(unittest.TestCase):
    def test_a_hungarian_voice_is_installed(self) -> None:
        self.assertTrue(available_voices("hu"), "no Hungarian system voice on this host")

    def test_it_synthesises_playable_audio(self) -> None:
        spoken = MacSayTts().speak("Készen állok.", language="hu-HU")

        self.assertEqual(spoken.engine_id, "macos-say")
        self.assertTrue(spoken.audio.startswith(b"RIFF"))
        self.assertGreater(len(spoken.audio), 1000)

    def test_empty_text_is_refused(self) -> None:
        with self.assertRaises(TtsError):
            MacSayTts().speak("   ", language="hu-HU")

    def test_an_unknown_voice_fails_closed(self) -> None:
        with self.assertRaises(TtsError):
            MacSayTts(voice="NoSuchVoiceExists").speak("Szia.", language="hu-HU")

    def test_shell_metacharacters_are_spoken_not_executed(self) -> None:
        # The text is an argument vector, never a shell string.
        marker = Path(tempfile.gettempdir()) / "bytewolf-say-injection-marker"
        marker.unlink(missing_ok=True)
        MacSayTts().speak(f"teszt; touch {marker}", language="hu-HU")
        self.assertFalse(marker.exists(), "text reached a shell")


if __name__ == "__main__":
    unittest.main()
