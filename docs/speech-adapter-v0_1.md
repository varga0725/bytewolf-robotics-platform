# Speech adapter v0.1 — decision and contract

The models are **already decided** in the project's canonical planning source
(Notion, *Speech Stack v0.1 döntés*, 2026-07-24). This document records that
decision, the evidence that supports and qualifies it, and the contract that
fences what speech is allowed to do.

**The safety line first.** Speech is an *input* to the Cognitive Runtime. A
recognised utterance becomes at most a `command_suggestion`; it travels the same
`draft_flight_request` → reviewed MissionSpec → SafetyGate → explicit approval
path as typed chat. Nothing spoken can shorten that path, and the transcript
contract is built so that a document claiming otherwise is schema-invalid.

## The decision

| Function | Primary | Fallback |
| --- | --- | --- |
| **STT** — speech to text | **NVIDIA Parakeet TDT 0.6B v3** | Whisper large-v3-turbo, server-side |
| **TTS** — text to speech | **Coqui XTTS-v2** | pre-generated fixed Hungarian system messages for critical states |

Both run locally. The recorded reasoning: Parakeet fits the platform's
NVIDIA server and Jetson direction, is a small model, can be tuned for low
latency, and suits a real-time robotics pipeline. XTTS-v2 produces usable
Hungarian, runs locally, is multilingual, and can reproduce a speaker from a
short sample — with voice cloning permitted only with documented consent and an
authorised sample.

## Evidence found while implementing

Independent research supports the STT half and sharpens one open question on the
TTS half.

**STT — the choice is well supported.** Riva/NIM `parakeet-tdt-0.6b-v3` lists
Hungarian among 25 European languages with automatic language detection, so the
primary STT needs no new vendor and no new secret: the platform already holds an
NVIDIA key. `canary-1b-v2` covers the same languages with a larger model, a
second NVIDIA-side option if Parakeet's Hungarian disappoints.

**TTS — NVIDIA could not have taken this half anyway.** The current Magpie TTS
Multilingual model covers en-US, fr-FR and es-US only, with no Hungarian. That
is why the two halves come from different vendors: TTS is the constraint, not
STT.

**⚠️ One concrete data point for the open licence item.** The plan already flags
that XTTS-v2's licence must be cleared legally before commercial use. The
specific obstacle is that the XTTS v2 model weights ship under the **Coqui
Public Model License (CPML), which is non-commercial**. That does not overturn
the decision — XTTS-v2 remains the right development choice, and Hungarian
quality is why it was picked — but it means the compliance item is a real
blocker for a commercial release, not a formality.

If that item forces a change, the licence-clean alternative found in the same
research is **Piper**: fully offline, 30+ languages including Hungarian,
real-time on a Raspberry Pi 5 *without a GPU* (so comfortably within a Jetson's
budget), permissive licence. Its weakness against XTTS-v2 is that it does not do
voice cloning from a short sample. Cloud options (ElevenLabs, Azure, Google) all
support Hungarian but put a network dependency in the voice path of a robot that
must work without one.

None of the accuracy figures circulating for these engines is a decision: they
are vendor claims or general benchmarks, not measurements on this platform's
microphone, room and vocabulary. The engines sit behind an adapter interface so
the choice can be re-made against a recorded Hungarian audio set, exactly as the
vision detector choice waits on a recorded clip.

## Targets to measure against

From the plan, these are acceptance targets, not achievements:

| Metric | Initial target |
| --- | --- |
| STT first partial result | ≤ 800 ms |
| STT final result | ≤ 1.5 s from end of speech |
| TTS first-audio latency | ≤ 1.5 s |
| Full response start | ≤ 3 s for a simple command |
| Hungarian command recognition rate | ≥ 90% in a controlled indoor environment |
| **False mission starts** | **0 accepted cases** |

The last one is the only metric the contract can enforce on its own, and it is
the reason the contract is shaped the way it is.

## Architectural rules this contract implements

- STT and TTS are **adapter/capability**, never part of the cognitive core.
- A voice instruction may not drive a motor, PX4 or a ROS 2 actuator. Every
  physical action still goes `MissionSpec → SafetyGate → Mission Orchestrator →
  Body Adapter`.
- An uncertain transcript makes the system **ask back**; it never starts a
  mission on a guess. The contract carries `confidence` and `language_confidence`
  so a consumer can apply that rule rather than infer it.
- Raw audio retention is **off by default**; the contract never carries audio,
  only an opaque `audio_ref` to locally retained evidence.
- Emergency commands need a **separate deterministic keyword and policy layer** —
  deliberately not part of this contract, because an emergency path built out of
  a probabilistic transcript is exactly the wrong construction.

## Push-to-talk and interruption

Continuous listening is not the v0.1 default: an always-open microphone is a
privacy surface, and it invites the model to react to speech never addressed to
it. v0.1 is push-to-talk.

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
  once the transcript exists.
- **Barge-in cancels speech output, never a mission.** Interrupting the robot
  mid-sentence stops the audio; it cannot cancel, alter or approve a flight,
  because speech has no path to the executor either way.
- **A partial transcript is never a command.** Only a final transcript may carry
  a `command_suggestion`, and even then it is a suggestion.
- **Silence is not consent.** A timeout in LISTENING returns to IDLE and produces
  nothing; it never falls back to assuming the last request.

## The contract

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

## What is not done

The adapters themselves. This delivers the decision, the contract and the
boundary; wiring Parakeet and XTTS-v2, recording a Hungarian audio set, and
measuring against the targets above belong with the hardware.
