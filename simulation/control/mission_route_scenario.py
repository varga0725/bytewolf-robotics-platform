"""Pure scoring for shielded mission-route SITL evidence."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from math import isfinite


MINIMUM_CONTROL_RATE_HZ = 2.5
MAXIMUM_GROUND_TRUTH_AGE_S = 0.5


@dataclass(frozen=True)
class MissionRouteSample:
    at_s: float
    nominal_speed_m_s: float
    verdict: str
    commanded_speed_m_s: float
    delivered: bool
    exact_translational_stop: bool
    clearance_m: float | None = None
    ground_truth_age_s: float | None = None
    sensed_distance_m: float | None = None


@dataclass(frozen=True)
class MissionGroundTruthSample:
    at_s: float
    clearance_m: float | None
    age_s: float | None


@dataclass(frozen=True)
class MissionRouteReport:
    scenario: str
    recorded_at: str
    samples: int
    ground_truth_samples: int
    sample_rate_hz: float | None
    interventions: int
    delivered_stop: bool
    minimum_clearance_m: float | None
    required_clearance_m: float
    goto_location_calls: int
    fallback_completed: tuple[str, ...]
    landed: bool
    route_reached: bool
    execution_error: str | None
    passed: bool
    findings: list[str] = field(default_factory=list)
    proof_level: str = "app+PX4-SITL+Gazebo-ground-truth"

    def as_document(self) -> dict[str, object]:
        return asdict(self)


def evaluate_mission_route_run(
    *,
    scenario: str,
    samples: Sequence[MissionRouteSample],
    ground_truth_samples: Sequence[MissionGroundTruthSample],
    required_clearance_m: float,
    flown_seconds: float,
    armed_at_s: float,
    ended_at_s: float,
    goto_location_calls: int,
    fallback_completed: Sequence[str],
    landed: bool,
    route_reached: bool,
    execution_error: str | None = None,
    recorded_at: str,
) -> MissionRouteReport:
    """Judge whether one run proves an actually shielded mission leg."""
    findings: list[str] = []
    interventions = [sample for sample in samples if sample.verdict != "clear"]
    delivered_stop_samples = [
        sample
        for sample in samples
        if sample.verdict == "insufficient_clearance"
        and sample.exact_translational_stop
        and sample.delivered
        and sample.sensed_distance_m is not None
        and isfinite(sample.sensed_distance_m)
    ]
    delivered_stop = bool(delivered_stop_samples)
    computed_stop = any(
        sample.verdict != "clear" and sample.exact_translational_stop
        for sample in samples
    )
    clearances = [
        sample.clearance_m
        for sample in ground_truth_samples
        if sample.clearance_m is not None and isfinite(sample.clearance_m)
    ]
    minimum_clearance = min(clearances) if clearances else None
    sample_rate = (
        round(len(samples) / flown_seconds, 2)
        if samples and isfinite(flown_seconds) and flown_seconds > 0
        else None
    )

    if not samples:
        findings.append("The route recorded no control samples.")
    if sample_rate is None or sample_rate < MINIMUM_CONTROL_RATE_HZ:
        findings.append(
            "The mission-route control loop did not sustain the required "
            f"{MINIMUM_CONTROL_RATE_HZ:g} Hz."
        )
    times = [sample.at_s for sample in samples]
    if any(not isfinite(value) for value in times) or any(
        later <= earlier for earlier, later in zip(times, times[1:])
    ):
        findings.append("Control sample timestamps are not finite and strictly increasing.")
    elif any(later - earlier > 0.4 for earlier, later in zip(times, times[1:])):
        findings.append("The mission-route control loop had a gap beyond the setpoint TTL.")
    if not any(sample.sensed_distance_m is not None for sample in samples):
        findings.append("The sensor never presented the obstacle to the route shield.")
    if not any(sample.verdict == "insufficient_clearance" for sample in samples):
        findings.append("No blocking verdict was caused by the static obstacle.")
    if not interventions:
        findings.append("The route shield never intervened.")
    if computed_stop and not delivered_stop:
        findings.append(
            "The shield computed a zero command, but it was not delivered to PX4."
        )
    elif not delivered_stop:
        findings.append("No blocking verdict produced a delivered zero command.")
    if minimum_clearance is None:
        findings.append("No finite Gazebo ground-truth clearance was recorded.")
    elif minimum_clearance < required_clearance_m:
        findings.append(
            f"The vehicle came within {minimum_clearance:.2f} m, inside the "
            f"{required_clearance_m:.2f} m twin limit."
        )
    if not ground_truth_samples or any(
        sample.clearance_m is None
        or not isfinite(sample.clearance_m)
        or sample.age_s is None
        or not isfinite(sample.age_s)
        or sample.age_s > MAXIMUM_GROUND_TRUTH_AGE_S
        for sample in ground_truth_samples
    ):
        findings.append(
            "Fresh finite Gazebo ground truth was not available throughout the armed interval."
        )
    ground_truth_times = [sample.at_s for sample in ground_truth_samples]
    if any(not isfinite(value) for value in ground_truth_times) or any(
        later <= earlier
        for earlier, later in zip(ground_truth_times, ground_truth_times[1:])
    ):
        findings.append("Ground-truth timestamps are not finite and strictly increasing.")
    elif any(
        later - earlier > MAXIMUM_GROUND_TRUTH_AGE_S
        for earlier, later in zip(ground_truth_times, ground_truth_times[1:])
    ):
        findings.append("Gazebo ground-truth coverage had a gap during the armed interval.")
    if (
        not isfinite(armed_at_s)
        or not isfinite(ended_at_s)
        or ended_at_s < armed_at_s
    ):
        findings.append("The armed interval boundaries are invalid.")
    elif (
        not ground_truth_times
        or ground_truth_times[0] - armed_at_s > MAXIMUM_GROUND_TRUTH_AGE_S
        or ended_at_s - ground_truth_times[-1] > MAXIMUM_GROUND_TRUTH_AGE_S
    ):
        findings.append(
            "Gazebo ground truth did not cover both boundaries of the armed interval."
        )
    if delivered_stop_samples and not any(
        sample.clearance_m is not None
        and sample.ground_truth_age_s is not None
        and isfinite(sample.ground_truth_age_s)
        and sample.ground_truth_age_s <= MAXIMUM_GROUND_TRUTH_AGE_S
        for sample in delivered_stop_samples
    ):
        findings.append(
            "No fresh Gazebo ground truth was recorded with the delivered obstacle stop."
        )
    if goto_location_calls:
        findings.append(
            f"The run issued {goto_location_calls} goto_location call(s), so it did "
            "not prove the Offboard mission path."
        )
    completed = tuple(fallback_completed)
    required_fallback = ("zero_velocity", "hold", "land")
    if completed != required_fallback:
        findings.append(
            "The terminal fallback did not complete in exact zero_velocity, hold, land order."
        )
    if not landed:
        findings.append("PX4 telemetry did not confirm landing.")
    if route_reached:
        findings.append("The route reached the target behind the obstacle.")
    if execution_error is not None:
        findings.append(f"The live route failed outside its expected blocked-path outcome: {execution_error}")

    return MissionRouteReport(
        scenario=scenario,
        recorded_at=recorded_at,
        samples=len(samples),
        ground_truth_samples=len(ground_truth_samples),
        sample_rate_hz=sample_rate,
        interventions=len(interventions),
        delivered_stop=delivered_stop,
        minimum_clearance_m=(
            None if minimum_clearance is None else round(minimum_clearance, 3)
        ),
        required_clearance_m=round(required_clearance_m, 3),
        goto_location_calls=goto_location_calls,
        fallback_completed=completed,
        landed=landed,
        route_reached=route_reached,
        execution_error=execution_error,
        passed=not findings,
        findings=findings,
    )


__all__ = [
    "MissionRouteReport",
    "MissionGroundTruthSample",
    "MissionRouteSample",
    "evaluate_mission_route_run",
]
