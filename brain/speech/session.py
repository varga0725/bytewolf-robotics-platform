"""The push-to-talk session: when the microphone is open, and what interrupting means.

Continuous listening is not the default. An always-open microphone is a privacy
surface, and it invites the agent to react to speech that was never addressed to
it. The button is the consent signal.

```text
            ┌──────── press ────────┐
   IDLE ────┤                       ├──> LISTENING ──(release / silence)──> TRANSCRIBING
            └──────── (no audio kept in IDLE)                                    │
                                                                                 v
   SPEAKING <──(reply ready)── THINKING <───────────── transcript ───────────────┘
      │
      └──(new press, or stop)──> barge-in: stop audio output, discard the
                                  unfinished utterance, return to LISTENING
```

The rules this class enforces:

* **No audio is retained in IDLE.** The buffer opens on press and is cleared the
  moment the utterance leaves.
* **Barge-in cancels speech output, never a mission.** Interrupting stops the
  audio; it cannot cancel, alter or approve a flight, because this class has no
  path to one.
* **Silence is not consent.** A timeout in LISTENING returns to IDLE and yields
  nothing; it never falls back to assuming the last request.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from enum import Enum


DEFAULT_SILENCE_TIMEOUT_S = 8.0
DEFAULT_MAX_UTTERANCE_S = 30.0


class PttState(str, Enum):
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    THINKING = "thinking"
    SPEAKING = "speaking"


class PttError(RuntimeError):
    """A transition that would break the session's guarantees."""


class PushToTalkSession:
    """Tracks one microphone session and the audio it is allowed to hold."""

    def __init__(
        self,
        *,
        silence_timeout_s: float = DEFAULT_SILENCE_TIMEOUT_S,
        max_utterance_s: float = DEFAULT_MAX_UTTERANCE_S,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._silence_timeout = timedelta(seconds=silence_timeout_s)
        self._max_utterance = timedelta(seconds=max_utterance_s)
        self._now = now
        self._state = PttState.IDLE
        self._buffer = bytearray()
        self._opened_at: datetime | None = None
        self._barge_ins = 0

    @property
    def state(self) -> PttState:
        return self._state

    @property
    def buffered_bytes(self) -> int:
        """How much audio is held. Always zero outside an open utterance."""
        return len(self._buffer)

    @property
    def barge_in_count(self) -> int:
        return self._barge_ins

    # -- transitions ------------------------------------------------------

    def press(self) -> PttState:
        """Open the microphone. Pressing while the agent speaks is a barge-in."""
        if self._state is PttState.SPEAKING:
            # Stop the audio output and listen again. The reply is abandoned;
            # nothing about a mission is touched, because nothing here can.
            self._barge_ins += 1
        elif self._state is not PttState.IDLE:
            raise PttError(f"Cannot start listening from {self._state.value}.")
        self._state = PttState.LISTENING
        self._buffer = bytearray()
        self._opened_at = self._now()
        return self._state

    def feed(self, chunk: bytes) -> None:
        """Add captured audio. Only ever accepted while listening."""
        if self._state is not PttState.LISTENING:
            raise PttError("Audio may only be captured while listening.")
        self._buffer.extend(chunk)

    def release(self) -> bytes:
        """Close the microphone and hand over the utterance.

        The buffer is cleared here, not later: the audio exists for exactly as
        long as it takes to become a transcript.
        """
        if self._state is not PttState.LISTENING:
            raise PttError("Nothing is being captured.")
        utterance = bytes(self._buffer)
        self._buffer = bytearray()
        self._opened_at = None
        if not utterance:
            # Pressing and releasing without speaking is not a request.
            self._state = PttState.IDLE
            return b""
        self._state = PttState.TRANSCRIBING
        return utterance

    def transcribed(self) -> PttState:
        if self._state is not PttState.TRANSCRIBING:
            raise PttError(f"Cannot finish transcription from {self._state.value}.")
        self._state = PttState.THINKING
        return self._state

    def speak(self) -> PttState:
        if self._state is not PttState.THINKING:
            raise PttError(f"Cannot start speaking from {self._state.value}.")
        self._state = PttState.SPEAKING
        return self._state

    def finish(self) -> PttState:
        """The reply finished playing, or the turn ended without one."""
        self._state = PttState.IDLE
        self._buffer = bytearray()
        self._opened_at = None
        return self._state

    def stop(self) -> PttState:
        """Barge-in by voice or button: stop speaking, return to idle."""
        if self._state is PttState.SPEAKING:
            self._barge_ins += 1
        return self.finish()

    # -- timeouts ---------------------------------------------------------

    def expired(self) -> bool:
        """Whether an open microphone has been open too long."""
        if self._state is not PttState.LISTENING or self._opened_at is None:
            return False
        open_for = self._now() - self._opened_at
        if open_for >= self._max_utterance:
            return True
        return not self._buffer and open_for >= self._silence_timeout

    def timeout(self) -> PttState:
        """Silence is not consent: yield nothing and return to idle."""
        self._buffer = bytearray()
        self._opened_at = None
        self._state = PttState.IDLE
        return self._state
