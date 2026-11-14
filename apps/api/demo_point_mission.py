"""Review the Monday demo and register it for the normal approve boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from brain.mission.demo_point_rtl import (
    MISSION_TYPE,
    DemoPointRtlError,
    authorize_demo_point_rtl,
    load_demo_point_rtl_config,
)
from brain.mission.demo_reviewed_plan import default_demo_plan_path, write_reviewed_demo_plan
from brain.safety.gate import SafetyGate
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


@dataclass(frozen=True)
class DemoPointRtlReview:
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


def review_demo_point_rtl() -> DemoPointRtlReview:
    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    config = load_demo_point_rtl_config()
    gate = SafetyGate(profile.flight_limits())
    try:
        authorize_demo_point_rtl(gate, config)
    except Exception as error:
        raise DemoPointRtlError(str(error)) from error
    mission_id = str(uuid4())
    plan_path = default_demo_plan_path(mission_id)
    write_reviewed_demo_plan(plan_path, config, mission_id=mission_id)
    summary = (
        f"demo-point-rtl: fel {config.takeoff_altitude_m:g} m, "
        f"pont ({config.north_m:g} m észak, {config.east_m:g} m kelet), RTL."
    )
    return DemoPointRtlReview(
        plan_id=plan_path.name,
        mission_id=mission_id,
        plan_path=plan_path,
        summary=summary,
    )
