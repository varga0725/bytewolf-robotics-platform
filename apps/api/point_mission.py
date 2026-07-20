"""Turn a point picked on the map into a reviewed, approvable MissionSpec.

A clicked coordinate is the one mission request that needs no language model:
the user already said exactly where, so inventing a natural-language round trip
would add a component that can misread them. The point goes straight into the
same MissionSpec v0.1 the compiler and SafetyGate already validate, and lands
on disk with the same approval proof the executor demands.

The free-text goal never enters the spec. The schema has no field for it and
must not grow one: a purpose the executor cannot act on has no business in the
document the executor reads. It is recorded beside the plan for audit and shown
back to the user instead.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

from brain.mission.commands import WaypointCommand
from brain.mission_spec.reviewed_plan import default_plan_path, write_reviewed_plan
from brain.mission_spec.survey import SurveyPatternError, survey_waypoints
from brain.mission_spec.validation import (
    MissionSafetyProfile,
    validate_and_compile_mission_spec,
)


MAX_GOAL_CHARS = 240
DEFAULT_HOLD_S = 5.0
BUILDER_NAME = "dashboard-map-point"


class PointMissionError(ValueError):
    """The picked point cannot become a mission the safety layer would approve."""


@dataclass(frozen=True)
class PointMission:
    """One reviewed plan on disk, plus what the user said it was for."""

    plan_id: str
    mission_id: str
    plan_path: Path
    goal: str
    steps: tuple[str, ...]
    summary: str
    # The route the compiler actually produced, in launch-relative metres. The
    # dashboard draws these rather than re-deriving the sweep pattern in the
    # browser: a second implementation of the pattern would eventually disagree
    # with the one that flies, and the preview would be showing a mission that
    # does not exist.
    waypoints: tuple[tuple[float, float], ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "mission_id": self.mission_id,
            "goal": self.goal,
            "steps": list(self.steps),
            "summary": self.summary,
            "waypoints": [{"north_m": north, "east_m": east} for north, east in self.waypoints],
        }


def build_point_mission_spec(
    *,
    north_m: float,
    east_m: float,
    altitude_m: float,
    profile: MissionSafetyProfile,
    hold_s: float = DEFAULT_HOLD_S,
    mission_id: str | None = None,
) -> dict[str, Any]:
    """Compose the go-there-and-come-back spec for one picked point.

    The shape is deliberate: climb, fly to the point, hold long enough for the
    arrival to be visible in telemetry and in the audit trail, then return home
    under PX4's own RTL. Ending with RTL rather than LAND means a mission that
    reached a far corner does not leave the vehicle there.
    """
    for name, value in (("north_m", north_m), ("east_m", east_m), ("altitude_m", altitude_m)):
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(float(value)):
            raise PointMissionError(f"A picked point needs a finite {name}.")
    if altitude_m <= 0:
        raise PointMissionError("A picked point needs a positive altitude.")
    return {
        "schema_version": "0.1",
        "mission_id": mission_id or str(uuid4()),
        "vehicle_id": profile.vehicle_id,
        "intent": "inspect_area",
        "constraints": {
            "max_altitude_m": profile.max_altitude_m,
            "max_speed_m_s": profile.max_speed_m_s,
            "max_radius_m": profile.max_radius_m,
            "minimum_battery_percent_to_start": profile.minimum_battery_percent_to_start,
            "loss_of_link_action": profile.loss_of_link_action,
        },
        "steps": [
            {"type": "TAKEOFF", "altitude_m": float(altitude_m)},
            {
                "type": "GOTO_LOCAL",
                "north_m": float(north_m),
                "east_m": float(east_m),
                "down_m": -float(altitude_m),
            },
            {"type": "HOLD", "duration_s": float(hold_s)},
            {"type": "RTL"},
        ],
        "abort_policy": {
            "on_timeout": "RTL",
            "on_low_battery": "RTL",
            "on_position_invalid": "LAND",
        },
    }


def build_survey_mission_spec(
    *,
    centre_north_m: float,
    centre_east_m: float,
    radius_m: float,
    spacing_m: float,
    altitude_m: float,
    profile: MissionSafetyProfile,
    mission_id: str | None = None,
) -> dict[str, Any]:
    """Compose a sweep of one area as a single reviewable step.

    The document stays readable — climb, sweep this circle, come home — while
    the compiler turns the sweep into individually gate-checked waypoints. The
    spec is v0.2 because v0.1 is frozen: a document means what its own version
    said it meant.
    """
    if altitude_m <= 0:
        raise PointMissionError("A survey needs a positive altitude.")
    return {
        "schema_version": "0.2",
        "mission_id": mission_id or str(uuid4()),
        "vehicle_id": profile.vehicle_id,
        "intent": "inspect_area",
        "constraints": {
            "max_altitude_m": profile.max_altitude_m,
            "max_speed_m_s": profile.max_speed_m_s,
            "max_radius_m": profile.max_radius_m,
            "minimum_battery_percent_to_start": profile.minimum_battery_percent_to_start,
            "loss_of_link_action": profile.loss_of_link_action,
        },
        "steps": [
            {"type": "TAKEOFF", "altitude_m": float(altitude_m)},
            {
                "type": "SURVEY_AREA",
                "centre_north_m": float(centre_north_m),
                "centre_east_m": float(centre_east_m),
                "radius_m": float(radius_m),
                "spacing_m": float(spacing_m),
                "altitude_m": float(altitude_m),
            },
            {"type": "RTL"},
        ],
        "abort_policy": {
            "on_timeout": "RTL",
            "on_low_battery": "RTL",
            "on_position_invalid": "LAND",
        },
    }


def review_survey_mission(
    *,
    centre_north_m: float,
    centre_east_m: float,
    radius_m: float,
    spacing_m: float,
    altitude_m: float,
    goal: str,
    profile: MissionSafetyProfile,
    plan_directory: Path | None = None,
) -> PointMission:
    """Validate a requested sweep and, if approved, write the reviewed plan."""
    try:
        legs = len(
            survey_waypoints(
                centre_north_m=centre_north_m,
                centre_east_m=centre_east_m,
                radius_m=radius_m,
                spacing_m=spacing_m,
            )
        )
    except SurveyPatternError as error:
        raise PointMissionError(str(error)) from error
    spec = build_survey_mission_spec(
        centre_north_m=centre_north_m,
        centre_east_m=centre_east_m,
        radius_m=radius_m,
        spacing_m=spacing_m,
        altitude_m=altitude_m,
        profile=profile,
    )
    return _review_and_write(
        spec,
        goal=goal,
        profile=profile,
        plan_directory=plan_directory,
        summary=(
            f"{radius_m:g} m sugarú terület felderítése {spacing_m:g} m-es sávokkal, "
            f"{altitude_m:g} m magasan — {legs} waypoint, majd visszatérés."
        ),
    )


def review_point_mission(
    *,
    north_m: float,
    east_m: float,
    altitude_m: float,
    goal: str,
    profile: MissionSafetyProfile,
    plan_directory: Path | None = None,
    hold_s: float = DEFAULT_HOLD_S,
) -> PointMission:
    """Validate a picked point and, if approved, write the reviewed plan.

    A refusal names the constraint that refused it. Nothing is written when the
    safety layer says no: an unapproved plan file on disk is a plan somebody can
    later mistake for an approved one.
    """
    spec = build_point_mission_spec(
        north_m=north_m, east_m=east_m, altitude_m=altitude_m, profile=profile, hold_s=hold_s
    )
    distance_m = (north_m**2 + east_m**2) ** 0.5
    return _review_and_write(
        spec,
        goal=goal,
        profile=profile,
        plan_directory=plan_directory,
        summary=(
            f"{altitude_m:g} m magasan {distance_m:.0f} m-re "
            f"(É {north_m:+.0f} m, K {east_m:+.0f} m), majd visszatérés."
        ),
    )


def _review_and_write(
    spec: dict[str, Any],
    *,
    goal: str,
    profile: MissionSafetyProfile,
    plan_directory: Path | None,
    summary: str,
) -> PointMission:
    """The one path from a composed spec to an approved plan on disk.

    Point and survey requests differ only in what they compose; they must not
    differ in how they are reviewed, written, or refused.
    """
    goal_text = _admit_goal(goal)
    report = validate_and_compile_mission_spec(spec, profile)
    if not report.approved or report.mission is None:
        raise PointMissionError(
            "; ".join(issue.message for issue in report.issues) or "The mission was not approved."
        )
    plan_path = (
        default_plan_path(spec["mission_id"])
        if plan_directory is None
        else plan_directory / f"{spec['mission_id']}.mission-spec.json"
    )
    write_reviewed_plan(plan_path, spec, BUILDER_NAME)
    _write_goal_record(plan_path, str(spec["mission_id"]), goal_text)
    return PointMission(
        plan_id=plan_path.name,
        mission_id=str(spec["mission_id"]),
        plan_path=plan_path,
        goal=goal_text,
        steps=tuple(str(step["type"]) for step in spec["steps"]),
        summary=summary,
        waypoints=tuple(
            (command.north_m, command.east_m)
            for command in report.mission.commands
            if isinstance(command, WaypointCommand)
        ),
    )


def _admit_goal(goal: str) -> str:
    goal_text = " ".join(goal.split())
    if not goal_text:
        raise PointMissionError("A mission needs a stated goal.")
    if len(goal_text) > MAX_GOAL_CHARS:
        raise PointMissionError(f"A mission goal must be at most {MAX_GOAL_CHARS} characters.")
    return goal_text


def _write_goal_record(plan_path: Path, mission_id: str, goal: str) -> None:
    """Keep the human purpose beside the machine plan, never inside it."""
    record = {
        "schema_version": "mission-goal-v0.1",
        "mission_id": mission_id,
        "plan_filename": plan_path.name,
        "goal": goal,
        "stated_at": datetime.now(UTC).isoformat(),
        "source": BUILDER_NAME,
    }
    plan_path.with_name(f"{plan_path.name}.goal.json").write_text(
        json.dumps(record, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
