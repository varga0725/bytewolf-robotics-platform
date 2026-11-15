"""Review the Monday multi-waypoint demo and register it for approval."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import hypot, isfinite
from pathlib import Path
from uuid import uuid4

from apps.api.map_mission_home import (
    assert_home_matches,
    effective_max_radius_m,
    load_snapshot_home,
    load_snapshot_px4_geofence_m,
    parse_map_home_reference,
    waypoint_commands_from_geographic,
)
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
    latitude_deg: float
    longitude_deg: float
    relative_altitude_m: float


@dataclass(frozen=True)
class DemoWaypointMissionMapRequest:
    takeoff_altitude_m: float
    waypoints: tuple[MapWaypointInput, ...]
    home: dict[str, object]
    goal: str = ""

    @staticmethod
    def from_dict(
        document: dict[str, object], *, default_altitude_m: float | None = None
    ) -> "DemoWaypointMissionMapRequest":
        if default_altitude_m is None:
            default_altitude_m = load_demo_waypoint_mission_config(
                profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
            ).takeoff_altitude_m
        takeoff_raw = document.get("takeoff_altitude_m", default_altitude_m)
        if not isinstance(takeoff_raw, (int, float)) or not isfinite(float(takeoff_raw)) or float(takeoff_raw) <= 0:
            raise DemoWaypointMissionError("takeoff_altitude_m must be a positive finite number.")
        raw_points = document.get("waypoints")
        if not isinstance(raw_points, list):
            raise DemoWaypointMissionError("waypoints must be a JSON array.")
        if len(raw_points) == 0:
            raise DemoWaypointMissionError("At least one waypoint is required.")
        if len(raw_points) > MAX_MAP_WAYPOINTS:
            raise DemoWaypointMissionError(f"At most {MAX_MAP_WAYPOINTS} waypoints are supported.")
        home_raw = document.get("home")
        if not isinstance(home_raw, dict):
            raise DemoWaypointMissionError("A PX4 home reference is required for map waypoints.")
        parsed: list[MapWaypointInput] = []
        for index, item in enumerate(raw_points):
            if not isinstance(item, dict):
                raise DemoWaypointMissionError(f"Waypoint {index + 1} must be an object.")
            latitude = item.get("latitude_deg")
            longitude = item.get("longitude_deg")
            altitude = item.get("relative_altitude_m", float(takeoff_raw))
            if not all(isinstance(value, (int, float)) and isfinite(float(value)) for value in (latitude, longitude)):
                raise DemoWaypointMissionError(f"Waypoint {index + 1} must include finite latitude_deg and longitude_deg.")
            if not isinstance(altitude, (int, float)) or not isfinite(float(altitude)) or float(altitude) <= 0:
                raise DemoWaypointMissionError(f"Waypoint {index + 1} altitude must be positive.")
            parsed.append(
                MapWaypointInput(
                    latitude_deg=float(latitude),
                    longitude_deg=float(longitude),
                    relative_altitude_m=float(altitude),
                )
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
            home=home_raw,
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


def review_demo_waypoint_mission_from_map(
    request: DemoWaypointMissionMapRequest,
    *,
    telemetry_path: Path,
) -> DemoWaypointMissionReview:
    proposed_home = parse_map_home_reference({"home": request.home})
    authority_home = load_snapshot_home(telemetry_path)
    assert_home_matches(proposed_home, authority=authority_home)

    geo_waypoints = [
        {
            "latitude_deg": item.latitude_deg,
            "longitude_deg": item.longitude_deg,
            "relative_altitude_m": item.relative_altitude_m,
        }
        for item in request.waypoints
    ]
    converted = waypoint_commands_from_geographic(
        home=authority_home,
        waypoints=tuple(geo_waypoints),
        default_altitude_m=request.takeoff_altitude_m,
    )
    commands = tuple(
        WaypointCommand(north_m, east_m, target_altitude_m=altitude_m)
        for north_m, east_m, altitude_m in converted
    )

    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    # PX4 refuses to arm when a mission item lies outside its own fence (COMMAND_DENIED
    # with no reason the console can show), so refuse here with one it can.
    radius_m = effective_max_radius_m(profile.max_radius_m, load_snapshot_px4_geofence_m(telemetry_path))
    for index, (north_m, east_m, _altitude_m) in enumerate(converted):
        distance_m = hypot(north_m, east_m)
        if distance_m > radius_m:
            raise DemoWaypointMissionError(
                f"Waypoint {index + 1} is {distance_m:.1f} m from PX4 home; "
                f"the active limit is {radius_m:g} m (twin.yaml and PX4 GF_MAX_HOR_DIST)."
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
    map_home_document = {
        "latitude_deg": authority_home.latitude_deg,
        "longitude_deg": authority_home.longitude_deg,
        "absolute_altitude_m": authority_home.absolute_altitude_m,
        "captured_at": authority_home.captured_at,
    }
    write_reviewed_demo_waypoint_plan(
        plan_path,
        config,
        mission_id=mission_id,
        map_home=map_home_document,
        geo_waypoints=geo_waypoints,
    )
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