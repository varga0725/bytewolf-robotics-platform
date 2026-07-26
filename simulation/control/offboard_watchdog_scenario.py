"""Interrupt a live Offboard stream and record what actually happens.

Phase C's boundary and watchdog are decided by unit tests. What unit tests
cannot answer is the question the roadmap actually asks: when the stream that
was flying the vehicle stops, does the vehicle end up somewhere safe? That needs
a real PX4, and this is the scenario that asks it.

The flight is deliberately small. Take off through the ordinary mission path,
hand over to a streamed body-frame velocity, hold that stream for a few seconds,
then *stop sending* -- not stop cleanly, simply go silent, the way a crashed
producer does. Two things are then recorded from two independent places:

* what this process concluded (the watchdog's state and fallback), and
* what PX4 did about it (its own flight mode, read from telemetry).

Those are separate on purpose. The watchdog deciding a fallback is our claim;
PX4 leaving Offboard is the vehicle's. A scenario that only recorded the first
would be marking its own homework -- and a stopped process cannot execute a
fallback at all, which is exactly why PX4's own failsafe has to be the authority
and has to be observed rather than assumed.

Scoring is a pure function of the recorded timeline, so it is unit-tested
without SITL. Only :func:`run_offboard_watchdog_scenario` touches PX4.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
import sys


#: How long the stream flies before it is cut. Long enough for PX4 to be
#: following it rather than still settling into the mode.
STREAM_SECONDS = 4.0

#: How long to keep watching after the last setpoint, commanding nothing.
#: PX4's offboard-loss timeout (COM_OF_LOSS_T) defaults to one second, so this
#: is far longer than the failsafe should need -- deliberately, because the
#: first run of this scenario showed PX4 sitting in OFFBOARD through eight
#: seconds of our silence, and a short window could not tell a slow failsafe
#: from an absent one.
OBSERVE_AFTER_SILENCE_S = 20.0

#: Modes PX4 may legitimately end up in after losing an Offboard stream. Every
#: one of them is a vehicle that has stopped following a dead commander.
SAFE_MODES_AFTER_LOSS = frozenset({"HOLD", "LOITER", "LAND", "RETURN", "RTL", "POSCTL", "ALTCTL"})


@dataclass(frozen=True)
class ModeSample:
    """One observation of PX4's flight mode."""

    at_s: float
    mode: str


@dataclass(frozen=True)
class OffboardWatchdogReport:
    """What the interrupted stream produced, from both sides."""

    scenario: str
    recorded_at: str
    #: Our side.
    setpoints_offered: int
    setpoints_approved: int
    setpoints_delivered: int
    watchdog_stopped_at_s: float | None
    fallback: list[str] = field(default_factory=list)
    rejections: dict[str, int] = field(default_factory=dict)
    #: PX4's side.
    mode_at_silence: str | None = None
    mode_after_silence: str | None = None
    px4_left_offboard_at_s: float | None = None
    #: When this process next commanded anything of its own. Everything at or
    #: after it is our doing, not PX4's, and cannot count as the vehicle
    #: reacting to the lost stream.
    intervened_at_s: float | None = None
    #: When the fallback the watchdog decided was actually carried out, and
    #: what each of its steps managed to do. PX4 leaving Offboard from here on
    #: is the design working, not an accident of cleanup.
    fallback_executed_at_s: float | None = None
    fallback_steps: dict | None = None
    mode_samples: list[ModeSample] = field(default_factory=list)
    #: The verdict, and what it is allowed to mean.
    passed: bool = False
    findings: list[str] = field(default_factory=list)
    proof_level: str = "app+SITL"

    def as_document(self) -> dict:
        document = asdict(self)
        document["mode_samples"] = [asdict(sample) for sample in self.mode_samples]
        return document


def evaluate_offboard_watchdog(
    *,
    setpoints_offered: int,
    setpoints_approved: int,
    setpoints_delivered: int,
    watchdog_stopped_at_s: float | None,
    fallback: Sequence[str],
    rejections: dict[str, int],
    mode_samples: Sequence[ModeSample],
    silence_began_at_s: float,
    recorded_at: str,
    intervened_at_s: float | None = None,
    fallback_executed_at_s: float | None = None,
    fallback_steps: dict | None = None,
) -> OffboardWatchdogReport:
    """Score an interrupted stream from its recorded timeline.

    A pure function of what was observed, so the verdict can be re-derived from
    the artifact rather than trusted because a run once printed it.
    """
    findings: list[str] = []
    at_silence = _mode_at(mode_samples, silence_began_at_s)
    # Only what happened while nobody was commanding anything counts as PX4
    # reacting. The first run of this scenario "passed" on a mode change that
    # was its own land command -- the scenario marking its own homework, which
    # is the one thing a SITL proof must not do.
    observed = _before_intervention(mode_samples, intervened_at_s)
    after = _last_mode(observed)
    left_at = _left_offboard_at(observed, silence_began_at_s)

    if setpoints_delivered == 0:
        findings.append("No setpoint reached PX4, so nothing was interrupted.")
    if at_silence != "OFFBOARD":
        findings.append(
            f"PX4 was in {at_silence or 'an unknown mode'} when the stream stopped, not OFFBOARD, "
            "so this run did not test an interrupted Offboard stream."
        )
    if watchdog_stopped_at_s is None:
        findings.append("The watchdog never declared the stream stopped.")
    if not fallback:
        findings.append("The watchdog produced no fallback.")
    elif fallback[0] != "zero_velocity":
        findings.append(f"The fallback began with {fallback[0]}, not zero_velocity.")
    if fallback_executed_at_s is None:
        findings.append("The fallback was never carried out, only decided.")
    elif fallback_steps is not None and "zero_velocity" not in fallback_steps.get("completed", []):
        findings.append(
            "The fallback ran but failed to stop the vehicle: zero_velocity did not complete."
        )
    if left_at is None:
        findings.append(
            "The vehicle never left OFFBOARD. MAVSDK re-sends the last setpoint at 20 Hz, so "
            "silence from the producer is not silence on the link: unless the fallback is "
            "executed, PX4 keeps flying the last command it was given."
        )
    elif after is not None and after not in SAFE_MODES_AFTER_LOSS:
        findings.append(f"PX4 settled in {after}, which is not a documented safe mode.")

    return OffboardWatchdogReport(
        scenario="offboard-watchdog-interruption",
        recorded_at=recorded_at,
        setpoints_offered=setpoints_offered,
        setpoints_approved=setpoints_approved,
        setpoints_delivered=setpoints_delivered,
        watchdog_stopped_at_s=watchdog_stopped_at_s,
        fallback=list(fallback),
        rejections=dict(sorted(rejections.items())),
        mode_at_silence=at_silence,
        mode_after_silence=after,
        px4_left_offboard_at_s=left_at,
        intervened_at_s=intervened_at_s,
        fallback_executed_at_s=fallback_executed_at_s,
        fallback_steps=fallback_steps,
        mode_samples=list(mode_samples),
        passed=not findings,
        findings=findings,
    )


def _mode_at(samples: Sequence[ModeSample], at_s: float) -> str | None:
    """The mode in force at an instant: the last sample at or before it."""
    seen = [sample for sample in samples if sample.at_s <= at_s]
    return seen[-1].mode if seen else None


def _last_mode(samples: Sequence[ModeSample]) -> str | None:
    return samples[-1].mode if samples else None


def _before_intervention(
    samples: Sequence[ModeSample], intervened_at_s: float | None
) -> Sequence[ModeSample]:
    """Only the part of the timeline this process did not cause."""
    if intervened_at_s is None:
        return samples
    return [sample for sample in samples if sample.at_s < intervened_at_s]


def _left_offboard_at(samples: Sequence[ModeSample], silence_began_at_s: float) -> float | None:
    """When PX4 first reported a non-OFFBOARD mode after the stream stopped."""
    for sample in samples:
        if sample.at_s >= silence_began_at_s and sample.mode != "OFFBOARD":
            return sample.at_s
    return None


def _finite_seconds(value: float) -> float:
    if not isfinite(value):
        raise ValueError("A scenario timestamp must be finite.")
    return round(value, 3)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--artifact-dir", type=Path,
        default=Path("simulation/artifacts/control"),
        help="Where the evidence artifact is written.",
    )
    parser.add_argument("--altitude-m", type=float, default=3.0)
    parser.add_argument("--stream-seconds", type=float, default=STREAM_SECONDS)
    parser.add_argument("--observe-seconds", type=float, default=OBSERVE_AFTER_SILENCE_S)
    parser.add_argument(
        "--startup-wait-s", type=float, default=32.0,
        help="How long PX4 and Gazebo get to come up before the flight starts.",
    )
    arguments = parser.parse_args(argv)

    from simulation.control.offboard_watchdog_run import run_offboard_watchdog_scenario

    report = run_offboard_watchdog_scenario(
        arguments.artifact_dir,
        altitude_m=arguments.altitude_m,
        stream_seconds=arguments.stream_seconds,
        observe_seconds=arguments.observe_seconds,
        startup_wait_s=arguments.startup_wait_s,
    )
    print(json.dumps(report.as_document(), indent=2))
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
