"""The speech adapters: recognisers, synthesisers and the push-to-talk session.

No model or network is involved: the NIM recogniser is driven through an httpx
mock transport, and every local engine is injected. What is actually asserted is
the behaviour that keeps speech honest -- an engine failure never becomes an
invented transcript, an uncertain utterance proposes nothing, and the microphone
holds audio only while the button is down.
"""

from datetime import UTC, datetime, timedelta
import unittest

import httpx

from brain.speech.adapters.stt import (
    FallbackStt,
    LocalWhisperStt,
    ParakeetNimStt,
    RawTranscription,
    SttError,
)
from brain.speech.adapters.tts import (
    FallbackTts,
    FixedMessageTts,
    SpokenAudio,
    TtsError,
    XttsTts,
)
from brain.speech.recognition import SpeechRecogniser
from brain.speech.session import PttError, PttState, PushToTalkSession
from brain.speech.transcript import TranscriptState


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
AUDIO = b"RIFF....WAVEfmt "


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class ParakeetSttTests(unittest.TestCase):
    def test_it_parses_the_openai_compatible_transcription(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("Authorization")
            return httpx.Response(200, json={"text": "  Mennyi az akku?  ", "language": "hu-HU"})

        engine = ParakeetNimStt(api_key="k", client=_client(handler))
        result = engine.transcribe(AUDIO, language="hu-HU")

        self.assertEqual(result.text, "Mennyi az akku?")
        self.assertTrue(result.is_final)
        self.assertTrue(seen["url"].endswith("/audio/transcriptions"))
        self.assertEqual(seen["auth"], "Bearer k")

    def test_an_http_error_is_a_refusal_not_an_empty_transcript(self) -> None:
        engine = ParakeetNimStt(api_key="k", client=_client(lambda _r: httpx.Response(503, json={})))
        with self.assertRaises(SttError):
            engine.transcribe(AUDIO, language="hu-HU")

    def test_a_body_without_text_is_refused(self) -> None:
        engine = ParakeetNimStt(api_key="k", client=_client(lambda _r: httpx.Response(200, json={"ok": 1})))
        with self.assertRaises(SttError):
            engine.transcribe(AUDIO, language="hu-HU")

    def test_empty_audio_is_refused_before_any_request(self) -> None:
        def handler(_request):  # pragma: no cover - must never be called
            raise AssertionError("no request should be made for empty audio")

        with self.assertRaises(SttError):
            ParakeetNimStt(api_key="k", client=_client(handler)).transcribe(b"", language="hu-HU")

    def test_from_env_refuses_without_a_key(self) -> None:
        with self.assertRaises(SttError):
            ParakeetNimStt.from_env({})


class FallbackSttTests(unittest.TestCase):
    class _Dead:
        engine_id = "dead"
        engine_version = "v0"

        def transcribe(self, audio, *, language):
            raise SttError("down")

    class _Works:
        engine_id = "works"
        engine_version = "v1"

        def transcribe(self, audio, *, language):
            return RawTranscription(text="szia", confidence=0.9)

    def test_it_falls_through_to_the_working_recogniser(self) -> None:
        chain = FallbackStt((self._Dead(), self._Works()))
        result = chain.transcribe(AUDIO, language="hu-HU")
        self.assertEqual(result.text, "szia")
        self.assertEqual(chain.served_by, "works")
        self.assertEqual(chain.engine_id, "works")

    def test_all_failing_names_every_engine(self) -> None:
        chain = FallbackStt((self._Dead(), self._Dead()))
        with self.assertRaises(SttError) as raised:
            chain.transcribe(AUDIO, language="hu-HU")
        self.assertIn("dead", str(raised.exception))


class LocalWhisperTests(unittest.TestCase):
    def test_it_reads_the_injected_model_result(self) -> None:
        engine = LocalWhisperStt(model=type("M", (), {"transcribe": lambda _s, a, language: {"text": " szia "}})())
        self.assertEqual(engine.transcribe(AUDIO, language="hu-HU").text, "szia")

    def test_an_engine_fault_is_a_refusal(self) -> None:
        class _Boom:
            def transcribe(self, audio, language):
                raise RuntimeError("no model")

        with self.assertRaises(SttError):
            LocalWhisperStt(model=_Boom()).transcribe(AUDIO, language="hu-HU")


class _StubEngine:
    engine_id = "stub"
    engine_version = "v1"

    def __init__(self, result=None, fail=False):
        self._result = result
        self._fail = fail

    def transcribe(self, audio, *, language):
        if self._fail:
            raise SttError("engine down")
        return self._result


class RecognitionTests(unittest.TestCase):
    def _recogniser(self, engine) -> SpeechRecogniser:
        return SpeechRecogniser(engine, now=lambda: NOW)

    def test_a_confident_final_utterance_may_suggest(self) -> None:
        recogniser = self._recogniser(_StubEngine(RawTranscription("Nézd meg az udvart.", confidence=0.9)))
        transcript = recogniser.recognise(AUDIO, transcript_id="utt-1")

        self.assertEqual(transcript.state(NOW), TranscriptState.VALID)
        self.assertIsNotNone(transcript.command_suggestion)
        self.assertTrue(transcript.command_suggestion.requires_approval)

    def test_an_uncertain_utterance_proposes_nothing(self) -> None:
        # The runtime is expected to ask back rather than act on a guess.
        recogniser = self._recogniser(_StubEngine(RawTranscription("Nézd meg az udvart.", confidence=0.2)))
        transcript = recogniser.recognise(AUDIO, transcript_id="utt-2")

        self.assertEqual(transcript.text, "Nézd meg az udvart.")
        self.assertIsNone(transcript.command_suggestion)

    def test_an_engine_reporting_no_confidence_is_not_confident(self) -> None:
        recogniser = self._recogniser(_StubEngine(RawTranscription("Szállj fel.", confidence=None)))
        self.assertIsNone(recogniser.recognise(AUDIO, transcript_id="utt-3").command_suggestion)

    def test_a_partial_utterance_proposes_nothing(self) -> None:
        recogniser = self._recogniser(_StubEngine(RawTranscription("Ne szállj", confidence=0.95, is_final=False)))
        self.assertIsNone(recogniser.recognise(AUDIO, transcript_id="utt-4").command_suggestion)

    def test_a_recogniser_failure_is_invalid_not_silence(self) -> None:
        # Silence would read as "nothing was said"; the turn must learn that
        # listening itself failed.
        transcript = self._recogniser(_StubEngine(fail=True)).recognise(AUDIO, transcript_id="utt-5")

        self.assertEqual(transcript.state(NOW), TranscriptState.INVALID)
        self.assertEqual(transcript.text, "")
        self.assertIsNone(transcript.command_suggestion)

    def test_an_empty_transcription_is_missing_not_valid(self) -> None:
        transcript = self._recogniser(_StubEngine(RawTranscription("   ", confidence=0.9))).recognise(
            AUDIO, transcript_id="utt-6"
        )
        self.assertEqual(transcript.state(NOW), TranscriptState.MISSING)

    def test_the_engine_and_audio_reference_are_recorded(self) -> None:
        recogniser = self._recogniser(_StubEngine(RawTranscription("szia", confidence=0.9)))
        transcript = recogniser.recognise(AUDIO, transcript_id="utt-7", audio_ref="clip-7", session_id="s-1")

        self.assertEqual(transcript.engine, {"id": "stub", "version": "v1"})
        self.assertEqual(transcript.audio_ref, "clip-7")
        self.assertEqual(transcript.session_id, "s-1")


class PushToTalkTests(unittest.TestCase):
    def _session(self, clock=None) -> PushToTalkSession:
        return PushToTalkSession(now=clock or (lambda: NOW))

    def test_a_full_turn_walks_the_state_machine(self) -> None:
        session = self._session()
        self.assertEqual(session.state, PttState.IDLE)
        session.press()
        session.feed(b"audio")
        utterance = session.release()
        self.assertEqual(utterance, b"audio")
        self.assertEqual(session.state, PttState.TRANSCRIBING)
        session.transcribed()
        session.speak()
        self.assertEqual(session.state, PttState.SPEAKING)
        session.finish()
        self.assertEqual(session.state, PttState.IDLE)

    def test_no_audio_is_retained_outside_an_open_utterance(self) -> None:
        session = self._session()
        session.press()
        session.feed(b"audio")
        session.release()
        self.assertEqual(session.buffered_bytes, 0)

    def test_audio_cannot_be_captured_while_idle(self) -> None:
        with self.assertRaises(PttError):
            self._session().feed(b"audio")

    def test_pressing_while_speaking_is_a_barge_in_that_returns_to_listening(self) -> None:
        session = self._session()
        session.press()
        session.feed(b"a")
        session.release()
        session.transcribed()
        session.speak()

        session.press()  # barge-in

        self.assertEqual(session.state, PttState.LISTENING)
        self.assertEqual(session.barge_in_count, 1)
        self.assertEqual(session.buffered_bytes, 0)

    def test_press_and_release_without_speaking_is_not_a_request(self) -> None:
        session = self._session()
        session.press()
        self.assertEqual(session.release(), b"")
        self.assertEqual(session.state, PttState.IDLE)

    def test_silence_times_out_and_yields_nothing(self) -> None:
        clock = [NOW]
        session = PushToTalkSession(silence_timeout_s=5.0, now=lambda: clock[0])
        session.press()
        self.assertFalse(session.expired())

        clock[0] = NOW + timedelta(seconds=6)
        self.assertTrue(session.expired())
        self.assertEqual(session.timeout(), PttState.IDLE)
        self.assertEqual(session.buffered_bytes, 0)

    def test_an_over_long_utterance_expires_even_while_speech_arrives(self) -> None:
        clock = [NOW]
        session = PushToTalkSession(max_utterance_s=10.0, now=lambda: clock[0])
        session.press()
        session.feed(b"still talking")
        clock[0] = NOW + timedelta(seconds=11)
        self.assertTrue(session.expired())


class TtsTests(unittest.TestCase):
    def test_xtts_synthesises_through_the_injected_model(self) -> None:
        class _Synth:
            def tts(self, text, language, **kwargs):
                assert language == "hu"  # a bare code, not 'hu-HU'
                return b"wav-bytes"

        spoken = XttsTts(synthesiser=_Synth()).speak("Szia!", language="hu-HU")
        self.assertIsInstance(spoken, SpokenAudio)
        self.assertEqual(spoken.audio, b"wav-bytes")
        self.assertEqual(spoken.engine_id, "coqui/xtts-v2")

    def test_an_engine_fault_is_a_refusal(self) -> None:
        class _Boom:
            def tts(self, **_kwargs):
                raise RuntimeError("no model")

        with self.assertRaises(TtsError):
            XttsTts(synthesiser=_Boom()).speak("Szia!", language="hu-HU")

    def test_the_fallback_speaks_only_what_was_recorded_in_advance(self) -> None:
        fixed = FixedMessageTts(clips={"Nem tudok most válaszolni.": b"clip"})
        self.assertEqual(fixed.speak("Nem tudok most válaszolni.", language="hu-HU").audio, b"clip")
        with self.assertRaises(TtsError):
            fixed.speak("Bármi más.", language="hu-HU")

    def test_the_chain_falls_back_to_fixed_messages(self) -> None:
        class _Dead:
            engine_id = "dead"
            engine_version = "v0"

            def speak(self, text, *, language):
                raise TtsError("down")

        chain = FallbackTts((_Dead(), FixedMessageTts(clips={"Készen állok.": b"clip"})))
        spoken = chain.speak("Készen állok.", language="hu-HU")
        self.assertEqual(spoken.engine_id, "fixed-messages")
        self.assertEqual(chain.served_by, "fixed-messages")


if __name__ == "__main__":
    unittest.main()
