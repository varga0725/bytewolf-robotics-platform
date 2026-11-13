"""Review the Monday multi-waypoint demo and register it for approval."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from brain.mission.demo_errors import DemoWaypointMissionError
from brain.mission.demo_waypoint_mission import (
    MISSION_TYPE,
    authorize_demo_waypoint_mission,
    load_demo_waypoint_mission_config,
)
from brain.mission.demo_waypoint_reviewed_plan import (
    default_demo_waypoint_plan_path,
    write_reviewed_demo_waypoint_plan,
)
from brain.safety.gate import SafetyGate, SafetyViolation
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


@dataclass(frozen=True)
class DemoWaypointMissionReview:
    plan_id: str
    mission_id: str
    plan_path: Path
    summary: str

    def as_dict(self) -> dict[str, object]:
        return {
            "mission_type": MISSION_TYPE,
            "plan_id": self.plan_id,
            "mission_id": self.mission_id,
            "summary": self.summary,
            "approval_required": True,
        }


def review_demo_waypoint_mission() -> DemoWaypointMissionReview:
    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    config = load_demo_waypoint_mission_config()
    gate = SafetyGate(profile.flight_limits())
    try:
        authorize_demo_waypoint_mission(gate, config)
    except SafetyViolation as error:
        raise DemoWaypointMissionError(str(error)) from error
    mission_id = str(uuid4())
    plan_path = default_demo_waypoint_plan_path(mission_id)
    write_reviewed_demo_waypoint_plan(plan_path, config, mission_id=mission_id)
    summary = (
        f"demo-waypoint-mission: fel {config.takeoff_altitude_m:g} m, "
        f"{len(config.waypoints)} pont, RTL."
    )
    return DemoWaypointMissionReview(
        plan_id=plan_path.name,
        mission_id=mission_id,
        plan_path=plan_path,
        summary=summary,
    )
