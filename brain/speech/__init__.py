"""Speech domain — STT/TTS and communication adapters.

Speech recognition produces command *suggestions* only; it never executes a
flight. A recognised command still travels ``draft_flight_request`` -> reviewed
MissionSpec -> SafetyGate -> explicit approval. The speech adapter is an input to
the Cognitive Runtime, not a control path.

See ``docs/workstreams/speech-adapter.md`` for the research scope, the transcript
contract sketch, the Definition of Done and the acceptance criteria.
"""

CONTRACT_VERSION = "v0.1"

__all__ = ["CONTRACT_VERSION"]
