# Speech live evidence — dev Mac, 2026-07-25

The first end-to-end run of the speech path on real engines. Proof level:
**app + local models on the development Mac** — no GPU host, no robot.

## What ran

```
macOS 'Tünde' (hu_HU)  ──speak──>  16 kHz PCM WAV
                                        │
                                        v
faster-whisper 'small' (local, offline) ──transcribe──> Hungarian text
                                        │
                                        v
SpeechRecogniser ──> transcript_v0_1 (validated) ──> command_suggestion
```

Measured, on the loopback (the system voice speaking, the recogniser listening).
The decided fallback model is `large-v3-turbo`; `small` is shown beside it
because the difference is the argument for the larger model:

| Spoken | `small` | `large-v3-turbo` |
| --- | --- | --- |
| Mennyi az akkumulátor töltöttsége? | "Mennyi az **akumulátor** **töltöcsége**?" (0.72) | "Mennyi az **akkumulátortöltötsége**?" (0.92) |
| Nézd meg kérlek mi van az udvaron. | "**Nézzb** meg, kérlek mi van az **útvaron**." (0.66) | "Nézd meg, kérlek, mi van az **útvaron**." (0.88) |
| Szállj fel két méterre és gyere vissza. | — | "Szállj fel két **métere** és gyere vissza!" (0.89) |

Language was detected as `hu` every time, and each transcript carried a
`command_suggestion` with `requires_approval: true`, validated by the contract
loader.

**Latency, model already loaded, CPU on an Apple Silicon Mac: ~4.3 s per
utterance** (4334 / 4335 / 4292 ms for the three above). The Speech Stack target
is ≤ 1.5 s for a final result, so this host misses it by roughly 3×. That is a
statement about the host, not the model: the decision put Parakeet on a GPU
server for exactly this reason, and the same Whisper model on a GPU is a
different measurement. Nothing here should be read as the target being met or
unreachable.

**On accuracy:** every one of the three transcripts is *semantically* right — the
errors are a merged compound, a doubled consonant, a single letter. A language
model reading these would understand all three correctly. That is not the same as
a word error rate, and no WER is claimed here.

## The finding that changes the plan

**The decided primary STT is not reachable from this machine.** The platform's
NVIDIA key serves `integrate.api.nvidia.com`, which is the LLM gateway: its model
list has 118 entries and **not one ASR or TTS model** (only `riva-translate`,
which is text translation). Three plausible speech endpoints were tried and all
returned 404.

So Parakeet TDT 0.6B v3 requires **self-hosting the NIM speech microservice on a
GPU host** — the RTX 3080 Ti server or the Jetson. The Speech Stack decision's
reasoning ("fits the NVIDIA server and Jetson direction") holds exactly as
written; what does not follow is the convenience that came with it. There is no
"same key, no new vendor" path on a laptop: until that GPU host exists, STT runs
on local Whisper, which is the decision's own documented fallback.

## What is honest about these numbers

- **Synthetic speech is not a benchmark.** The recogniser was listening to a
  speech synthesiser, in a silent room, through no microphone, with no drone
  noise and no room reverberation. The ≥90% Hungarian command recognition target
  cannot be claimed from this and was not measured. Real microphone input is
  wired (`brain/speech/adapters/microphone.py`) but needs a host with microphone
  permission to run.
- **Confidence is a heuristic.** faster-whisper reports average log-probability,
  converted here to a 0-1 figure so it can be compared to a threshold. It is not
  a calibrated probability, and the 0.6 suggestion threshold above it is a
  starting point.
- **One host, one run each.** Three utterances on one machine is a wiring proof,
  not a measurement with variance.

## The macOS voice is a dev-box tool, not a decision

`say -v Tünde` is offline, Hungarian and free, which makes it the right voice for
the development machine and a convenient generator of Hungarian test audio. It
only exists on macOS, so it cannot follow the stack onto a Jetson. The decided
TTS remains XTTS-v2, with its licence question still open.

One real defect surfaced while wiring it: **`say` does not fail on an unknown
voice** — it quietly substitutes the system default, which would read Hungarian
text in an English voice and sound like a bug nobody could trace. The adapter now
checks the voice is installed and refuses otherwise.

## Reproducing

```
say -v Tünde -o /tmp/hu.wav --data-format=LEI16@16000 "Mennyi az akkumulátor töltöttsége?"
python -c "
from pathlib import Path
from brain.speech.adapters.faster_whisper_stt import FasterWhisperStt
print(FasterWhisperStt(model_size='small').transcribe(Path('/tmp/hu.wav').read_bytes(), language='hu-HU').text)
"
```

The first run downloads the model; nothing leaves the machine after that.

## Running a real turn

Microphone capture goes through ffmpeg's AVFoundation input, so there is no new
Python audio dependency and the output is already 16 kHz mono PCM. Every capture
is bounded; there is no continuous-listening mode to enable by accident.

```
python -m brain.cli.speech_turn --list-devices
python -m brain.cli.speech_turn --seconds 4 --model large-v3-turbo --speak-back
```

On macOS the recording process needs microphone permission, and a process
spawned by an agent's shell tool does not inherit it — run this from a terminal
that holds the grant, exactly as with the camera.

The turn records, transcribes, publishes a validated transcript and can read the
result back in Hungarian. It cannot fly anything: a recognised request becomes a
`command_suggestion` carrying `requires_approval`, and turning that into a
mission is the Cognitive Runtime's job through reviewed MissionSpec, the
SafetyGate and an explicit approval.
