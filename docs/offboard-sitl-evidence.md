# Offboard watchdog — SITL evidence, 2026-07-26

Phase C's boundary and watchdog were decided by unit tests. This is the run that
asked the question unit tests cannot: when the stream flying the vehicle stops,
where does the vehicle end up?

**Proof level: app + PX4 SITL.** No physical vehicle, no measured airframe.

## What it found before it passed

The first run passed, and the pass was wrong. Worth recording, because the way
it was wrong is the most useful thing here.

The scenario streamed a body-frame velocity, went silent, and observed that PX4
left Offboard — into `LAND`, at 20.5 s. It looked like a clean failsafe. It was
the scenario's own cleanup `land()` call, issued the moment the observation
window closed. **The scenario was marking its own homework.**

Fixed by recording when this process next commands anything and discounting
everything from that instant on. Re-run with a twenty-second silent window and
nothing commanded:

```
 8.1 s  OFFBOARD          the stream has the vehicle
12.9 s  (silence begins)  the producer stops sending
13.1 s  watchdog: STOPPED fallback decided: zero_velocity, hold, land
   …    OFFBOARD          for the full twenty seconds
```

**PX4 never intervened.** The vehicle sat in Offboard, still creeping forward on
a 0.3 m/s command whose author had stopped talking.

## Why, and why it matters

From MAVSDK's own documentation:

> Mavsdk automatically sends setpoints at 20Hz (PX4 Offboard mode requires that
> setpoints are minimally sent at 2Hz).

**The producer going silent does not make the link go silent.** MAVSDK keeps
re-sending the last setpoint at 20 Hz, so PX4 sees a perfectly healthy stream and
does exactly what it is told: keeps flying. Our watchdog detected the silence
correctly and decided the right fallback — and nothing executed it, so nothing
changed.

That is precisely the coasting failure the whole boundary exists to prevent, and
it survived the boundary because *detection and action are different things*.

It also sharpens what PX4's failsafe is and is not the authority for:

| Failure | Who acts |
| --- | --- |
| The process dies | PX4. MAVSDK dies with it, the re-send stops, the failsafe fires. |
| The **producer** goes silent while the process lives | **Us.** PX4 sees no loss to react to. |

The platform has always said "a stopped process commands nothing; PX4's failsafe
is the authority for that case". That remains true. What this run showed is that
it does not cover the case a runtime shield actually cares about.

## The fix

`brain/adapters/offboard_fallback.py` — an executor that performs the twin's
sequence rather than reporting it:

- `zero_velocity` — first, and first for a reason: the only step that stops the
  vehicle without depending on a mode change being accepted.
- `hold` — leaves Offboard, which is what actually ends MAVSDK's 20 Hz re-send.
  Without it every later step competes with a stream still commanding.
- `land` / `rtl` — as the twin declares.

Best effort **per step**, not overall: a later step is still attempted when an
earlier one fails, because the sequence is ordered by how much it gives up.
Stopping at the first failure would leave the vehicle holding a velocity because
one mode change happened to error.

It lives outside `brain/control` for the usual reason — it reaches `action` and
the mode switch, and the package deciding whether a setpoint may fly must not be
able to reach either.

## The passing run

All three artifacts are kept, because the two that did not pass are the evidence
that the third means something:

| Artifact | Verdict |
| --- | --- |
| `offboard-watchdog-20260726T085602Z.json` | passed — **wrongly**, on its own cleanup `land()` |
| `offboard-watchdog-20260726T085900Z.json` | failed honestly: PX4 stayed in Offboard for 20 s |
| `offboard-watchdog-20260726T090307Z.json` | **passed, for the right reason** |

The passing run records:

```
 0.9 s  TAKEOFF
 7.9 s  HOLD                  PX4's own auto-hold on reaching takeoff altitude
 8.9 s  OFFBOARD              40 setpoints offered, 40 approved, 40 delivered
12.9 s  (silence begins)
13.1 s  watchdog: STOPPED     fallback executed: zero_velocity ✓ hold ✓ land ✓
13.9 s  LAND                  PX4 left Offboard 0.86 s after the fallback ran
20.9 s  HOLD                  settled
32.2 s  (cleanup, discounted)
```

Every transition in the artifact is here, including the auto-hold at 7.9 s that
an earlier draft of this table skipped as uninteresting. It is uninteresting --
and a table that quietly tidies a timeline is not evidence, because a reader
cannot tell which omissions were judgement and which were mistakes.

Zero rejections: every setpoint the scenario produced was inside the envelope,
which is what makes the interruption the only variable.

## What this does not prove

- **Not a physical flight.** SITL is not an airframe, and no X500 V2 parameter
  in `twin.yaml` has been measured — every one is still `provenance: simulated`.
- **One run.** A single pass is not a reliability figure. The P0 matrices exist
  because ten runs say something one cannot.
- **Not a shield.** This is the boundary a shield would act through. Phase D is
  untouched.
- **The 0.86 s** between the fallback and PX4's mode change is one observation on
  one machine, not a latency budget.
- **The scenario enables Offboard itself.** The shipped twin keeps
  `safety.offboard.enabled: false`; the override lives in the scenario so the
  default stays off for every other caller.

## Reproducing

```zsh
.venv/bin/python -m simulation.control.offboard_watchdog_scenario \
    --artifact-dir simulation/artifacts/control
```

Starts and tears down its own PX4 and Gazebo. Exits non-zero when the run fails
its own scoring, which is a pure function of the recorded timeline and is
unit-tested without SITL — so the verdict can be re-derived from the artifact
rather than trusted because a run once printed it.
