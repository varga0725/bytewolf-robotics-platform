"""Review the Monday multi-waypoint demo and register it for approval."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from uuid import uuid4

from brain.mission.demo_errors import DemoWaypointMissionError
from brain.mission.commands import WaypointCommand
from brain.mission.demo_waypoint_mission import (
    MISSION_TYPE,
    authorize_demo_waypoint_mission,
    demo_waypoint_config_from_map,
    load_demo_waypoint_mission_config,
)
from brain.mission.demo_waypoint_reviewed_plan import (
    default_demo_waypoint_plan_path,
    write_reviewed_demo_waypoint_plan,
)
from brain.safety.gate import SafetyGate, SafetyViolation
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


MAX_MAP_GOAL_CHARS = 240
MAX_MAP_WAYPOINTS = 11


@dataclass(frozen=True)
class MapWaypointInput:
    north_m: float
    east_m: float
    relative_altitude_m: float | None = None


@dataclass(frozen=True)
class DemoWaypointMissionMapRequest:
    takeoff_altitude_m: float
    waypoints: tuple[MapWaypointInput, ...]
    goal: str = ""

    @staticmethod
    def from_dict(document: dict[str, object], *, default_altitude_m: float) -> "DemoWaypointMissionMapRequest":
        takeoff_raw = document.get("takeoff_altitude_m", default_altitude_m)
        if not isinstance(takeoff_raw, (int, float)) or not isfinite(float(takeoff_raw)) or float(takeoff_raw) <= 0:
            raise DemoWaypointMissionError("takeoff_altitude_m must be a positive finite number.")
        raw_points = document.get("waypoints")
        if not isinstance(raw_points, list) or not raw_points:
            raise DemoWaypointMissionError("At least one waypoint is required.")
        if len(raw_points) > MAX_MAP_WAYPOINTS:
            raise DemoWaypointMissionError(f"At most {MAX_MAP_WAYPOINTS} waypoints are supported.")
        parsed: list[MapWaypointInput] = []
        for index, item in enumerate(raw_points):
            if not isinstance(item, dict):
                raise DemoWaypointMissionError(f"Waypoint {index + 1} must be an object.")
            north = item.get("north_m")
            east = item.get("east_m")
            altitude = item.get("relative_altitude_m", float(takeoff_raw))
            if not all(isinstance(value, (int, float)) and isfinite(float(value)) for value in (north, east, altitude)):
                raise DemoWaypointMissionError(f"Waypoint {index + 1} coordinates must be finite numbers.")
            if float(altitude) <= 0:
                raise DemoWaypointMissionError(f"Waypoint {index + 1} altitude must be positive.")
            parsed.append(
                MapWaypointInput(north_m=float(north), east_m=float(east), relative_altitude_m=float(altitude))
            )
        goal = document.get("goal", "")
        if goal is not None and not isinstance(goal, str):
            raise DemoWaypointMissionError("goal must be a string when present.")
        goal_text = " ".join((goal or "").split())
        if goal_text and len(goal_text) > MAX_MAP_GOAL_CHARS:
            raise DemoWaypointMissionError(f"A mission goal must be at most {MAX_MAP_GOAL_CHARS} characters.")
        return DemoWaypointMissionMapRequest(
            takeoff_altitude_m=float(takeoff_raw),
            waypoints=tuple(parsed),
            goal=goal_text,
        )


@dataclass(frozen=True)
class DemoWaypointMissionReview:
    plan_id: str
    mission_id: str
    plan_path: Path
    summary: str
    goal: str = ""
    steps: tuple[str, ...] = field(default_factory=tuple)
    waypoints: tuple[tuple[float, float], ...] = field(default_factory=tuple)

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "mission_type": MISSION_TYPE,
            "plan_id": self.plan_id,
            "mission_id": self.mission_id,
            "summary": self.summary,
            "approval_required": True,
        }
        if self.goal:
            payload["goal"] = self.goal
        if self.steps:
            payload["steps"] = list(self.steps)
        if self.waypoints:
            payload["waypoints"] = [
                {"north_m": north, "east_m": east} for north, east in self.waypoints
            ]
        return payload


def review_demo_waypoint_mission() -> DemoWaypointMissionReview:
    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    config = load_demo_waypoint_mission_config(profile=profile)
    gate = SafetyGate(profile.flight_limits())
    try:
        authorize_demo_waypoint_mission(gate, config, profile=profile)
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


def review_demo_waypoint_mission_from_map(request: DemoWaypointMissionMapRequest) -> DemoWaypointMissionReview:
    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    commands = tuple(
        WaypointCommand(
            north_m=item.north_m,
            east_m=item.east_m,
            target_altitude_m=item.relative_altitude_m or request.takeoff_altitude_m,
        )
        for item in request.waypoints
    )
    config = demo_waypoint_config_from_map(
        takeoff_altitude_m=request.takeoff_altitude_m,
        waypoints=commands,
        profile=profile,
    )
    gate = SafetyGate(profile.flight_limits())
    try:
        authorize_demo_waypoint_mission(gate, config, profile=profile)
    except SafetyViolation as error:
        raise DemoWaypointMissionError(str(error)) from error
    mission_id = str(uuid4())
    plan_path = default_demo_waypoint_plan_path(mission_id)
    write_reviewed_demo_waypoint_plan(plan_path, config, mission_id=mission_id)
    summary = (
        f"Többpontos terv: fel {config.takeoff_altitude_m:g} m, "
        f"{len(config.waypoints)} waypoint, RTL és leszállás."
    )
    steps = (
        "TAKEOFF",
        *[f"GOTO_{index + 1}" for index in range(len(config.waypoints))],
        "RTL",
        "LAND",
    )
    return DemoWaypointMissionReview(
        plan_id=plan_path.name,
        mission_id=mission_id,
        plan_path=plan_path,
        summary=summary,
        goal=request.goal or summary,
        steps=steps,
        waypoints=tuple((waypoint.north_m, waypoint.east_m) for waypoint in config.waypoints),
    )
