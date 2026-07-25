# Speech adapter v0.1 — research and decision

The speech workstream's first deliverable is a decision, not a pipeline: which
STT and TTS engines a Hungarian-speaking robot can actually stand on, and what
the contract between speech and the Cognitive Runtime must forbid.

**The safety line first.** Speech is an *input* to the Cognitive Runtime. A
recognised utterance becomes at most a `command_suggestion`; it travels the same
`draft_flight_request` → reviewed MissionSpec → SafetyGate → explicit approval
path as typed chat. Nothing spoken can shorten that path, and the transcript
contract is built so that a document claiming otherwise is schema-invalid.

## The finding that shapes the design

The platform already runs on NVIDIA NIM, so the obvious first move is to use
NVIDIA for speech too. That works for **listening** and fails for **speaking**:

- **ASR:** Riva/NIM `parakeet-tdt-0.6b-v3` and `canary-1b-v2` both list Hungarian
  among 25 European languages, with automatic language detection.
- **TTS:** the current Magpie TTS Multilingual model covers **en-US, fr-FR and
  es-US only** — no Hungarian.

So the two halves cannot come from one vendor. TTS is the constraint, not STT.

## STT candidates

| Engine | Hungarian | Runs where | Latency shape | Licence / cost | Notes |
| --- | --- | --- | --- | --- | --- |
| **NVIDIA NIM Parakeet TDT 0.6B v3** | yes (25 EU langs, auto-detect) | cloud NIM, or self-hosted | streaming-capable, small model | same NVIDIA key the platform already holds | **Recommended first adapter**: no new vendor, no new secret |
| NVIDIA Canary 1B v2 | yes (25 EU langs) | cloud NIM, or self-hosted | larger, slower than Parakeet | same key | fallback if Parakeet's Hungarian disappoints |
| Whisper large-v3 (local, faster-whisper) | yes, unbenchmarked here | fully offline, GPU-friendly | batch-first; streaming needs chunking | permissive (MIT) | **the offline path**, and what a Jetson would run without a network |
| ElevenLabs Scribe | vendor claims 3.1% WER FLEURS-hu, 5.5% Common Voice | cloud only | low | paid, new vendor | strongest *claimed* Hungarian accuracy, but a vendor claim, not an independent benchmark |

## TTS candidates

| Engine | Hungarian | Runs where | Licence | Notes |
| --- | --- | --- | --- | --- |
| **Piper** | yes (30+ langs) | fully offline, real-time on a Pi 5 **without a GPU** | permissive | **Recommended**: the only candidate that is offline, Hungarian, embedded-friendly and licence-clean |
| ElevenLabs | yes | cloud only | paid | best quality, but a network dependency in the voice path of a robot |
| Azure / Google TTS | yes | cloud only | paid | mature, multilingual, same network objection |
| Coqui XTTS v2 | yes (17 langs) | offline | **CPML — non-commercial** | ⚠️ licence blocks the platform's commercial path; usable for experiments only |
| macOS `AVSpeechSynthesizer` | yes | offline, macOS only | OS-bundled | fine on the dev Mac, useless on the Jetson |

## Recommendation

- **STT: NVIDIA NIM Parakeet** as the first adapter (no new vendor, no new
  secret, Hungarian supported), with **local Whisper** kept as the offline
  profile for the Jetson.
- **TTS: Piper**, because it is the only option that is simultaneously offline,
  Hungarian, real-time without a GPU, and licence-clean. Cloud TTS stays a
  fallback for quality comparison, never the default voice of a robot that must
  work without a network.
- **Coqui XTTS v2 is rejected for production** on licence grounds (CPML,
  non-commercial), matching the plan's own "licenc és kereskedelmi
  használhatóság" decision criterion.

None of these numbers is a decision on quality: the Hungarian WER figures above
are vendor claims or general-purpose benchmarks, not measurements on this
platform's microphone, room and vocabulary. The engines are behind an adapter
interface precisely so the choice can be re-made against a recorded Hungarian
audio set, the same way the vision detector choice waits on a recorded clip.

## Push-to-talk and interruption

Continuous listening is not the v0.1 default: a robot that is always recording
is a privacy surface, and an open microphone invites the model to react to
speech that was never addressed to it. v0.1 is push-to-talk.

```text
            ┌──────── press ────────┐
   IDLE ────┤                       ├──> LISTENING ──(release / silence)──> TRANSCRIBING
            └──────── (no audio kept in IDLE)                                    │
                                                                                 v
   SPEAKING <──(reply ready)── THINKING <───────────── transcript ───────────────┘
      │
      └──(new press, or "stop")──> barge-in: stop audio output, discard the
                                    unfinished utterance, return to LISTENING
```

Rules the prototype must honour:

- **No audio is retained in IDLE.** The buffer opens on press and is discarded
  after the transcript is produced.
- **Barge-in cancels speech output, never a mission.** Interrupting the robot
  mid-sentence stops the audio; it cannot cancel, alter or approve a flight,
  because speech has no path to the executor either way.
- **A partial transcript is never a command.** Only a final transcript may carry
  a `command_suggestion`, and even then it is a suggestion.
- **Silence is not consent.** A timeout in LISTENING returns to IDLE and produces
  nothing; it never falls back to "assume the last request".

## Contract

`shared/schemas/speech/transcript_v0_1.schema.json`, loaded fail-closed by
`brain/speech/transcript.py`. It carries the utterance, the engine and version
that produced it, the language, freshness (`observed_at` + `max_age_s` +
`validity`, resolved by the consumer as with every other observation contract),
and optionally one `command_suggestion`.

The suggestion is where the safety line is written into the schema:

- it holds free text and a confidence, never a structured command, mission or
  actuator field;
- `requires_approval` is `const true`, so a document asserting that a spoken
  request needs no approval cannot validate;
- `is_final` must be true before a suggestion may appear — a partial transcript
  cannot propose anything.

Raw audio never enters the contract. `audio_ref` is an opaque reference to
locally retained evidence, on the same principle as the vision artifact
reference.
