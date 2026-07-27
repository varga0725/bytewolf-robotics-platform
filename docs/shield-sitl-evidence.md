# Runtime shield — SITL shadow evidence, 2026-07-26

The roadmap's **G4 gate** asks for numbers before the shield is allowed to act.
This is the shadow-mode run that produces them: four scenarios, each isolating
one thing the shield claims, against a real PX4, Gazebo and 2D lidar.

**Proof level: app + PX4 SITL.** Shadow mode — the shield observed and recorded,
and never constrained a command. No physical vehicle, no measured airframe.

## Results — 4/4

| Scenario | Samples | Interventions | Rate | False stops | Verdict |
| --- | --- | --- | --- | --- | --- |
| `clear-path` | 106 | 0 | 0.0 | **0** | passed |
| `static-obstacle` | 106 | 20 | 0.189 | 0 | passed |
| `blind-sector` | 106 | 106 | 1.0 | 0 | passed |
| `stale-sensor` | 107 | 52 | 0.486 | 0 | passed |

**The number that decides whether the shield is usable at all is the first row.**
A shield that stops for nothing is the one that gets switched off; 106 samples on
a clear path produced not one intervention.

**The number that decides whether it works is `clearance_at_first_intervention_m`
= 3.63 m** on the static obstacle, against a 2.00 m required standoff. The shield
raised the alarm with 1.6 m to spare — it would have stopped the vehicle in time.

The minimum clearance on that run was 1.65 m, *inside* the standoff, and this is
correct: shadow mode does not constrain, so the vehicle kept flying the nominal
0.6 m/s command straight past the point the shield objected. That figure measures
the planner, not the shield.

## Shadow and active are judged differently, on purpose

This took a wrong answer to get right. The first scored run failed on "the
vehicle came within 1.63 m" — blaming a shadow-mode shield for an outcome it was
explicitly forbidden to influence.

- **Shadow** is judged on **timing**: was the alarm raised while there was still
  room? That is the question the gate needs answered *before* enabling.
- **Active** is judged on **outcome**: where did the vehicle actually stop?

## What each scenario isolates

**`clear-path`** — nothing placed, nothing to see. Measures false stops.

**`static-obstacle`** — a 2×4×3 m box at 12 m on the flight path. The lidar sees
it, the shield objects at 3.63 m, and clearance is measured from **Gazebo ground
truth** rather than from the estimator the shield was reading. Asking the same
source twice would answer a different question.

**`blind-sector`** — a command *backwards*, into the 90 degrees a 270-degree
lidar cannot see. Nothing is there. All 106 samples refused, on coverage alone:
unobserved is not clear, and the blind spot makes that a live constraint.

**`stale-sensor`** — scans stop arriving mid-flight. 55 clear samples while the
sensor lived, then 52 `stale_observation` after the cut. The obstacle map was
still in memory and still said the path ahead was clear; the shield stopped
trusting it.

## Two harness bugs the runs found

Both are worth recording, because both would have produced confident nonsense.

**The scenario tested nothing and passed.** The first attempt returned 109
straight `no_observation` verdicts, `minimum_clearance_m: null` — and passed,
because it had "intervened" every time. The lidar had never published: the
`lidar-2d` profile spawns into Gazebo's `baylands` world, not `default`, so every
topic name was wrong. Fixed by pinning `PX4_GZ_WORLD` the way the existing
obstacle scenario already does — and an empty world matters for a second reason:
in `baylands` the lidar also sees terrain and buildings, so "the shield stopped
for the box we placed" would be unprovable.

The scoring now refuses a run whose samples are *all* sensor failures. It
measured fail-closed behaviour, which is real, but not the thing it claimed.

**The harness laundered stale data as fresh.** `stale-sensor` reported 107 clear
samples and zero interventions. The scans had stopped, but every observation was
stamped `observed_at=now()`, so the frozen map was re-dated as current on every
loop — defeating precisely the freshness check under test. Observations now carry
the capture file's modification time; when the capture dies the timestamp stops
advancing and the map ages out on its own.

## What this does not prove

- **Shadow, not active.** Nothing was constrained. That the shield *would* have
  stopped in time is an inference from timing, not an observation of a stop.
- **Not a physical flight.** Every `twin.yaml` parameter is still
  `provenance: simulated`; the braking figure and reaction latency the stopping
  distance rests on are conservative stand-ins, not measurements.
- **One run per scenario.** Not a reliability figure. The P0 matrices exist
  because ten runs say what one cannot.
- **One obstacle, static, in an empty world.** No moving obstacles, no clutter,
  no narrow gaps.
- **Horizontal only.** A 2D scan says nothing about the vertical axis, and the
  shield does not pretend otherwise.

## Next gate

G4 asks for shadow KPIs *and* fail-closed tests before active mode. The
fail-closed side is covered here. What is missing before enabling:

1. Repeatability — the same matrix ten times, as P0 does.
2. An **active** run of `static-obstacle`, judged on where the vehicle actually
   stopped.
3. The architecture review the Offboard task's own acceptance criteria require.

## Reproducing

```zsh
.venv/bin/python -m simulation.control.shield_cli --mode shadow
```

Each scenario starts and tears down its own PX4 and Gazebo. Exits non-zero when
any run fails its own scoring, which is a pure function of the recorded samples
and unit-tested without SITL.
