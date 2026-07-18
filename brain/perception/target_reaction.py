"""Turn a trusted target observation into a proposed move the SafetyGate re-checks.

This is the mission-reaction stage of the V1 perception flow, and it is built the
same way as the natural-language gateway: perception proposes, the deterministic
safety layer decides, and nothing here commands. It produces a WaypointCommand --
the same immutable type the flight adapter already consumes -- and runs it through
the existing SafetyGate, opening no new control path and emitting no MAVLink.

Two fail-closed guards stand before the gate. A target observation is acted on
only when it is VALID and fresh, so a stale, invalid, or missing fix reaches no
proposal at all. And a fix whose horizontal uncertainty is too large -- a target
seen from so high that a pixel projects to metres of ground error -- is refused
before it becomes a waypoint, rather than sending the vehicle toward a guess.

The target offset is relative to the vehicle now, while the gate reasons in
launch-relative metres, so the reaction adds the vehicle's current launch-relative
position before handing the waypoint to the gate. The gate is still the authority
on distance, geofence, and altitude; this stage only decides whether there is a
trustworthy target to propose reaching.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import isfinite

from brain.mission.commands import WaypointCommand
from brain.perception.target_estimator import TargetObservation
from brain.safety.gate import SafetyGate, SafetyViolation


# Above this horizontal uncertainty the fix is too loose to move toward; the
# reaction fails closed rather than chasing a guess.
DEFAULT_MAX_UNCERTAINTY_M = 3.0


@dataclass(frozen=True)
class ReactionRejection:
    """Why a target was not turned into a move, in terms a caller can act on."""

    reason: str
    detail: str | None = None


@dataclass(frozen=True)
class TargetReaction:
    """The outcome of reacting to a target: an approved move, or a refusal."""

    accepted: bool
    waypoint: WaypointCommand | None
    target_label: str | None
    rejection: ReactionRejection | None


def react_to_target(
    observation: TargetObservation,
    *,
    vehicle_north_m: float,
    vehicle_east_m: float,
    gate: SafetyGate,
    now: datetime,
    approach_altitude_m: float,
    max_uncertainty_m: float = DEFAULT_MAX_UNCERTAINTY_M,
) -> TargetReaction:
    """Propose a waypoint over a trusted target, or refuse with a reason.

    The returned waypoint is whatever the SafetyGate approved; nothing here runs
    it. An untrusted or too-uncertain target, or one the gate rejects, yields a
    structured refusal and no move.
    """
    state = observation.state(now)
    if not state.usable:
        return _refused(f"The target is {state.value} and cannot be acted on.")

    if not (isfinite(vehicle_north_m) and isfinite(vehicle_east_m)):
        return _refused("The vehicle's own position is not finite, so a target move cannot be framed.")
    if not (isfinite(approach_altitude_m) and approach_altitude_m > 0.0):
        return _refused("The approach altitude must be a positive, finite value.")

    uncertainty = observation.horizontal_uncertainty_m
    if uncertainty is not None and uncertainty > max_uncertainty_m:
        return _refused(
            f"The target fix is too uncertain to move toward: {uncertainty:.2f} m exceeds the "
            f"{max_uncertainty_m:.2f} m limit.",
            detail="horizontal_uncertainty",
        )

    offset_north_m, offset_east_m = observation.usable_offset_m(now)
    waypoint = WaypointCommand(
        north_m=vehicle_north_m + offset_north_m,
        east_m=vehicle_east_m + offset_east_m,
        target_altitude_m=approach_altitude_m,
    )
    try:
        decision = gate.evaluate(waypoint)
    except SafetyViolation as violation:
        return _refused(str(violation), detail="safety_gate")
    return TargetReaction(
        accepted=True, waypoint=decision.command, target_label=observation.label, rejection=None
    )


def _refused(reason: str, detail: str | None = None) -> TargetReaction:
    return TargetReaction(
        accepted=False, waypoint=None, target_label=None, rejection=ReactionRejection(reason, detail)
    )
