"""Pure acceptance scoring for a shielded obstacle-bypass mission leg."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot, isfinite
from collections.abc import Sequence


_MINIMUM_CONTROL_RATE_HZ = 2.5


@dataclass(frozen=True)
class AvoidanceRouteSample:
    at_s: float
    primary_verdict: str
    selected_mode: str
    commanded_x_m_s: float
    commanded_y_m_s: float
    commanded_z_m_s: float
    delivered: bool
    exact_translational_stop: bool


@dataclass(frozen=True)
class AvoidanceGroundTruthSample:
    at_s: float
    x_m: float | None
    y_m: float | None
    clearance_m: float | None
    age_s: float | None


@dataclass(frozen=True)
class AvoidanceRouteReport:
    samples: int
    sample_rate_hz: float | None
    minimum_clearance_m: float | None
    maximum_bypass_offset_m: float | None
    ground_truth_samples: int
    goto_location_calls: int
    fallback_used: bool
    landed: bool
    execution_error: str | None
    route_reached: bool
    passed: bool
    trace: list[AvoidanceRouteSample] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)


def evaluate_avoidance_route_run(
    *,
    samples: Sequence[AvoidanceRouteSample],
    ground_truth_samples: Sequence[AvoidanceGroundTruthSample],
    flown_seconds: float,
    armed_at_s: float,
    ended_at_s: float,
    required_clearance_m: float,
    required_bypass_offset_m: float,
    goto_location_calls: int,
    route_reached: bool,
    fallback_used: bool,
    landed: bool,
    execution_error: str | None,
) -> AvoidanceRouteReport:
    """Require evidence of a safe, delivered detour to the fixed goal."""
    findings: list[str] = []
    sample_rate = (
        round(len(samples) / flown_seconds, 2)
        if samples and isfinite(flown_seconds) and flown_seconds > 0.0
        else None
    )
    if not samples:
        findings.append("The avoidance route recorded no control samples.")
    if sample_rate is None or sample_rate < _MINIMUM_CONTROL_RATE_HZ:
        findings.append("The avoidance control loop did not sustain 2.5 Hz.")
    times = [sample.at_s for sample in samples]
    if any(not isfinite(value) for value in times) or any(
        later <= earlier for earlier, later in zip(times, times[1:])
    ):
        findings.append("Avoidance sample timestamps are not strictly increasing.")
    elif any(later - earlier > 0.4 for earlier, later in zip(times, times[1:])):
        findings.append("The avoidance control loop exceeded the setpoint TTL gap.")

    blocked = [
        sample for sample in samples if sample.primary_verdict == "insufficient_clearance"
    ]
    lateral_index = next(
        (
            index
            for index, sample in enumerate(samples)
            if sample.delivered
            and sample.selected_mode in {"left", "right"}
            and abs(sample.commanded_y_m_s) > 0.0
        ),
        None,
    )
    lateral_delivered = lateral_index is not None
    stop_delivered = lateral_index is not None and any(
        sample.delivered
        and sample.primary_verdict == "insufficient_clearance"
        and sample.exact_translational_stop
        for sample in samples[:lateral_index]
    )
    if not blocked:
        findings.append("No direct-path blocking obstacle was observed.")
    if not stop_delivered:
        findings.append("No delivered stop preceded the detour.")
    if not lateral_delivered:
        findings.append("No delivered lateral detour command was recorded.")

    clearances = [
        sample.clearance_m
        for sample in ground_truth_samples
        if sample.clearance_m is not None and isfinite(sample.clearance_m)
    ]
    minimum_clearance = min(clearances) if clearances else None
    if minimum_clearance is None:
        findings.append("No finite ground-truth clearance was recorded.")
    elif minimum_clearance < required_clearance_m:
        findings.append("The avoidance route violated the required clearance.")

    offsets = [
        abs(sample.x_m)
        for sample in ground_truth_samples
        if sample.x_m is not None and isfinite(sample.x_m)
    ]
    maximum_offset = max(offsets) if offsets else None
    if maximum_offset is None or maximum_offset < required_bypass_offset_m:
        findings.append("The vehicle never reached the required bypass offset.")
    gt_times = [sample.at_s for sample in ground_truth_samples]
    if not ground_truth_samples or any(
        sample.x_m is None or sample.y_m is None or sample.clearance_m is None
        or sample.age_s is None or not isfinite(sample.x_m) or not isfinite(sample.y_m)
        or not isfinite(sample.clearance_m) or not isfinite(sample.age_s)
        or sample.age_s < 0.0 or sample.age_s > 0.5
        for sample in ground_truth_samples
    ):
        findings.append("Fresh finite ground truth was not available throughout the armed interval.")
    if not isfinite(armed_at_s) or not isfinite(ended_at_s) or ended_at_s < armed_at_s:
        findings.append("The armed interval boundaries are invalid.")
    elif any(not isfinite(value) for value in gt_times) or any(
        later <= earlier for earlier, later in zip(gt_times, gt_times[1:])
    ):
        findings.append("Ground-truth timestamps are not finite and strictly increasing.")
    elif not gt_times or gt_times[0] < armed_at_s or gt_times[-1] > ended_at_s or gt_times[0] - armed_at_s > 0.5 or ended_at_s - gt_times[-1] > 0.5:
        findings.append("Ground truth did not cover the armed interval boundaries.")
    elif any(later - earlier > 0.5 for earlier, later in zip(gt_times, gt_times[1:])):
        findings.append("Ground truth coverage had a gap during the armed interval.")
    if goto_location_calls:
        findings.append("The run used goto_location instead of the Offboard route.")
    if not route_reached:
        findings.append("The original mission target was not reached after avoidance.")
    if fallback_used:
        findings.append("A safety fallback was required during the avoidance route.")
    if not landed:
        findings.append("PX4 telemetry did not confirm a normal terminal landing.")
    if execution_error is not None:
        findings.append(f"The avoidance route failed: {execution_error}")
    return AvoidanceRouteReport(
        samples=len(samples),
        sample_rate_hz=sample_rate,
        minimum_clearance_m=(
            None if minimum_clearance is None else round(minimum_clearance, 3)
        ),
        maximum_bypass_offset_m=(
            None if maximum_offset is None else round(maximum_offset, 3)
        ),
        ground_truth_samples=len(ground_truth_samples),
        goto_location_calls=goto_location_calls,
        fallback_used=fallback_used,
        landed=landed,
        execution_error=execution_error,
        route_reached=route_reached,
        passed=not findings,
        findings=findings,
        trace=list(samples),
    )


__all__ = [
    "AvoidanceRouteReport",
    "AvoidanceGroundTruthSample",
    "AvoidanceRouteSample",
    "evaluate_avoidance_route_run",
]
