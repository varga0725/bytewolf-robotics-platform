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
    delivered: bool
    clearance_m: float | None
    lateral_offset_m: float | None


@dataclass(frozen=True)
class AvoidanceRouteReport:
    samples: int
    sample_rate_hz: float | None
    minimum_clearance_m: float | None
    maximum_bypass_offset_m: float | None
    route_reached: bool
    passed: bool
    findings: list[str] = field(default_factory=list)


def evaluate_avoidance_route_run(
    *,
    samples: Sequence[AvoidanceRouteSample],
    flown_seconds: float,
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
    stop_delivered = any(
        sample.delivered
        and sample.primary_verdict == "insufficient_clearance"
        and sample.commanded_x_m_s == 0.0
        and sample.commanded_y_m_s == 0.0
        for sample in samples
    )
    lateral_delivered = any(
        sample.delivered
        and sample.selected_mode in {"left", "right"}
        and abs(sample.commanded_y_m_s) > 0.0
        for sample in samples
    )
    if not blocked:
        findings.append("No direct-path blocking obstacle was observed.")
    if not stop_delivered:
        findings.append("No delivered stop preceded the detour.")
    if not lateral_delivered:
        findings.append("No delivered lateral detour command was recorded.")

    clearances = [
        sample.clearance_m
        for sample in samples
        if sample.clearance_m is not None and isfinite(sample.clearance_m)
    ]
    minimum_clearance = min(clearances) if clearances else None
    if minimum_clearance is None:
        findings.append("No finite ground-truth clearance was recorded.")
    elif minimum_clearance < required_clearance_m:
        findings.append("The avoidance route violated the required clearance.")

    offsets = [
        abs(sample.lateral_offset_m)
        for sample in samples
        if sample.lateral_offset_m is not None and isfinite(sample.lateral_offset_m)
    ]
    maximum_offset = max(offsets) if offsets else None
    if maximum_offset is None or maximum_offset < required_bypass_offset_m:
        findings.append("The vehicle never reached the required bypass offset.")
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
        route_reached=route_reached,
        passed=not findings,
        findings=findings,
    )


__all__ = [
    "AvoidanceRouteReport",
    "AvoidanceRouteSample",
    "evaluate_avoidance_route_run",
]
