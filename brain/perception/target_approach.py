"""Close the V1 perception loop: a camera frame becomes a safety-checked move.

This is the single seam the autonomous "see it and go to it" behaviour turns on.
It composes the three already-built and ground-truth-confirmed stages -- detect,
estimate, react -- into one decision: given a down-camera frame and where the
vehicle is now, produce either a ``WaypointCommand`` the SafetyGate approved, or
a structured refusal that names why no move was proposed.

Nothing here flies or emits MAVLink. It is a pure function of its inputs, so the
unit tests drive it with synthetic marker frames, and the live scenario
(:mod:`simulation.perception.autonomous_approach`) wraps a real takeoff, goto,
and land around the same decision. Perception proposes; the SafetyGate inside
:func:`brain.perception.target_reaction.react_to_target` remains the authority
that approves or refuses the move.

Every stage fails closed. A frame with no trustworthy marker, a target seen from
too much tilt or an unknown altitude, a fix too uncertain to chase, or a move the
gate rejects -- each resolves to a decision a caller cannot mistake for an
approved waypoint, and none of them reaches the vehicle.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from brain.mission.commands import WaypointCommand
from brain.perception.camera_frame import CameraFrame
from brain.perception.detector import DetectionResult, DetectorAdapter
from brain.perception.target_estimator import (
    GlobalFix,
    GroundTargetEstimator,
    TargetObservation,
)
from brain.perception.target_reaction import (
    DEFAULT_MAX_UNCERTAINTY_M,
    TargetReaction,
    react_to_target,
)
from brain.safety.gate import SafetyGate


@dataclass(frozen=True)
class ApproachDecision:
    """The full record of one perception-to-move decision, for audit and reuse.

    It carries every intermediate stage, not just the outcome, so a live run can
    log what the detector saw and what the estimator projected alongside the
    gate's verdict. ``accepted`` and ``waypoint`` mirror the reaction so callers
    need not reach through it for the common case.
    """

    detection: DetectionResult
    observation: TargetObservation
    reaction: TargetReaction

    @property
    def accepted(self) -> bool:
        return self.reaction.accepted

    @property
    def waypoint(self) -> WaypointCommand | None:
        return self.reaction.waypoint

    @property
    def refusal_reason(self) -> str | None:
        return None if self.reaction.rejection is None else self.reaction.rejection.reason


def plan_target_approach(
    frame: CameraFrame | None,
    *,
    detector: DetectorAdapter,
    estimator: GroundTargetEstimator,
    gate: SafetyGate,
    altitude_agl_m: float,
    vehicle_north_m: float,
    vehicle_east_m: float,
    now: datetime,
    approach_altitude_m: float,
    yaw_deg: float = 0.0,
    tilt_deg: float = 0.0,
    global_position: GlobalFix | None = None,
    max_uncertainty_m: float = DEFAULT_MAX_UNCERTAINTY_M,
) -> ApproachDecision:
    """Turn one down-camera frame into a safety-checked move, or a refusal.

    The chain is deterministic and side-effect free: the detector analyses the
    frame, the estimator projects the most confident detection to a ground
    offset, and the reaction adds the vehicle's launch-relative position and
    hands the waypoint to the SafetyGate. Any stage that cannot vouch for its
    result fails closed, so the decision is either an approved waypoint or a
    named refusal -- never a guess dressed as a command.
    """
    detection = detector.analyze(frame)
    observation = estimator.estimate(
        detection,
        altitude_agl_m=altitude_agl_m,
        now=now,
        yaw_deg=yaw_deg,
        tilt_deg=tilt_deg,
        global_position=global_position,
    )
    reaction = react_to_target(
        observation,
        vehicle_north_m=vehicle_north_m,
        vehicle_east_m=vehicle_east_m,
        gate=gate,
        now=now,
        approach_altitude_m=approach_altitude_m,
        max_uncertainty_m=max_uncertainty_m,
    )
    return ApproachDecision(detection=detection, observation=observation, reaction=reaction)
