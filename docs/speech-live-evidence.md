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

Measured, on the loopback (the system voice speaking, the recogniser listening):

| Spoken | Recognised | Confidence | State |
| --- | --- | --- | --- |
| "Mennyi az akkumulátor töltöttsége?" | "Mennyi az akumulátor töltöcsége?" | 0.72 | valid |
| "Nézd meg kérlek mi van az udvaron." | "Nézzb meg, kérlek mi van az útvaron." | 0.66 | valid |

Language was detected as `hu` in both cases, and the resulting transcript carried
a `command_suggestion` with `requires_approval: true`, validated by the contract
loader.

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

- **Both transcripts contain errors** ("akumulátor", "töltöcsége", "Nézzb",
  "útvaron"). This is the `small` model; the decided fallback is
  `large-v3-turbo`, which is materially better and is what a measurement should
  use. `small` was chosen here to prove the wiring, not the accuracy.
- **Synthetic speech is not a benchmark.** The recogniser was listening to a
  speech synthesiser, in a silent room, with no microphone, no drone noise and no
  room reverberation. The Speech Stack targets (≥90% Hungarian command
  recognition, ≤1.5 s final result) cannot be claimed from this and were not
  measured.
- **Confidence is a heuristic.** faster-whisper reports average log-probability,
  converted here to a 0-1 figure so it can be compared to a threshold. It is not
  a calibrated probability, and the 0.6 suggestion threshold above it is a
  starting point.

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
