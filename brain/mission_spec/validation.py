"""Deterministic validation and compilation for the MissionSpec boundary.

Two versions are live. v0.1 is frozen; v0.2 adds `SURVEY_AREA`, the first step
that states an *area* instead of a place. A frozen contract does not grow a new
step type — a v0.1 document means exactly what it meant when it was written —
so the version in the document selects its schema, and an unknown version is
refused rather than guessed at.
"""

from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.mission_spec.survey import SurveyPatternError, survey_reach_m, survey_waypoints
from brain.safety.gate import FlightLimits, LocalPolygonGeofence, SafetyGate
from brain.safety.profile import SafetyProfile, load_safety_profile


_SCHEMA_DIRECTORY = Path(__file__).resolve().parents[2] / "shared/schemas/mission_spec"
_SCHEMA_PATHS = {
    "0.1": _SCHEMA_DIRECTORY / "mission_spec_v0_1.schema.json",
    "0.2": _SCHEMA_DIRECTORY / "mission_spec_v0_2.schema.json",
}
_VALIDATORS = {
    version: Draft202012Validator(json.loads(path.read_text()), format_checker=FormatChecker())
    for version, path in _SCHEMA_PATHS.items()
}
# Kept for callers that only ever spoke v0.1; the default stays the frozen one.
_SCHEMA_PATH = _SCHEMA_PATHS["0.1"]
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_VALIDATOR = _VALIDATORS["0.1"]


def _validator_for(document: object) -> Draft202012Validator | None:
    """Pick the schema the document itself declares, or refuse to guess."""
    version = document.get("schema_version") if isinstance(document, Mapping) else None
    return _VALIDATORS.get(version) if isinstance(version, str) else None


@dataclass(frozen=True)
class MissionSafetyProfile:
    """Hard platform limits that a MissionSpec may tighten but never relax."""

    vehicle_id: str
    max_altitude_m: float
    max_speed_m_s: float
    max_radius_m: float
    minimum_battery_percent_to_start: float
    loss_of_link_action: str
    # The fence is a platform limit like the others, and it was the one this
    # shape dropped. Every MissionSpec route — the dashboard's map, the chat
    # agent, the Telegram gateway — compiled against altitude and radius alone,
    # so the tighter of the twin's two horizontal bounds was enforced on the
    # hand-written CLIs and nowhere else. A mission cannot state a fence of its
    # own, so this comes from the profile and never from the document.
    allowed_geofence: LocalPolygonGeofence | None = None


def load_mission_safety_profile(path: Path | str) -> MissionSafetyProfile:
    """Load the active twin contract in the MissionSpec validation shape."""
    return _mission_safety_profile(load_safety_profile(path))


def _mission_safety_profile(profile: SafetyProfile) -> MissionSafetyProfile:
    return MissionSafetyProfile(
        vehicle_id=profile.vehicle_id,
        max_altitude_m=profile.max_altitude_m,
        max_speed_m_s=profile.max_speed_m_s,
        max_radius_m=profile.max_radius_m,
        minimum_battery_percent_to_start=profile.minimum_battery_percent_to_start,
        loss_of_link_action=profile.loss_of_link_action,
        allowed_geofence=profile.allowed_geofence,
    )


@dataclass(frozen=True)
class ValidationIssue:
    path: tuple[str | int, ...]
    message: str


@dataclass(frozen=True)
class CompiledMission:
    """Immutable high-level commands; this object has no PX4 or MAVSDK access."""

    mission_id: str
    vehicle_id: str
    source_hash: str
    commands: tuple[TakeoffCommand | WaypointCommand | ReturnToHomeCommand | LandCommand, ...]
    hold_durations_s: tuple[float, ...]
    terminal_action: str
    abort_policy: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class ValidationReport:
    approved: bool
    issues: tuple[ValidationIssue, ...]
    mission: CompiledMission | None


def validate_and_compile_mission_spec(
    document: Mapping[str, Any], profile: MissionSafetyProfile
) -> ValidationReport:
    """Validate a mission before it can be handed to any flight adapter."""
    source = dict(document)
    validator = _validator_for(source)
    if validator is None:
        return ValidationReport(
            approved=False,
            issues=(
                _issue(
                    ("schema_version",),
                    "Unknown MissionSpec schema version; a document is only read by the "
                    "contract it declares.",
                ),
            ),
            mission=None,
        )
    schema_issues = tuple(
        ValidationIssue(tuple(error.absolute_path), error.message)
        for error in sorted(validator.iter_errors(source), key=_schema_error_sort_key)
    )
    if schema_issues:
        return ValidationReport(approved=False, issues=schema_issues, mission=None)

    semantic_issues = _semantic_issues(source, profile)
    if semantic_issues:
        return ValidationReport(approved=False, issues=semantic_issues, mission=None)

    return ValidationReport(
        approved=True,
        issues=(),
        mission=_compile(source, profile),
    )


def _schema_error_sort_key(error: Any) -> tuple[tuple[str, ...], str]:
    return tuple(str(item) for item in error.absolute_path), error.message


def _issue(path: tuple[str | int, ...], message: str) -> ValidationIssue:
    return ValidationIssue(path=path, message=message)


def _semantic_issues(
    source: Mapping[str, Any], profile: MissionSafetyProfile
) -> tuple[ValidationIssue, ...]:
    constraints = source["constraints"]
    steps = source["steps"]
    assert isinstance(constraints, Mapping)
    assert isinstance(steps, list)
    issues: list[ValidationIssue] = []

    _check_finite_numbers(issues, source)

    if source["vehicle_id"] != profile.vehicle_id:
        issues.append(_issue(("vehicle_id",), "Mission vehicle does not match the active twin."))

    _check_platform_ceiling(
        issues, constraints, "max_altitude_m", profile.max_altitude_m, "platform maximum altitude"
    )
    _check_platform_ceiling(
        issues, constraints, "max_speed_m_s", profile.max_speed_m_s, "platform maximum speed"
    )
    _check_platform_ceiling(
        issues, constraints, "max_radius_m", profile.max_radius_m, "platform maximum radius"
    )
    minimum_battery = float(constraints["minimum_battery_percent_to_start"])
    if not isfinite(minimum_battery) or minimum_battery < profile.minimum_battery_percent_to_start:
        issues.append(
            _issue(
                ("constraints", "minimum_battery_percent_to_start"),
                "Mission battery reserve may not be lower than the platform minimum.",
            )
        )
    if constraints["loss_of_link_action"] != profile.loss_of_link_action:
        issues.append(
            _issue(
                ("constraints", "loss_of_link_action"),
                "Mission link-loss action must match the platform safety action.",
            )
        )

    types = [step["type"] for step in steps]
    if types[0] != "TAKEOFF":
        issues.append(_issue(("steps", 0, "type"), "The first step must be TAKEOFF."))
    if types.count("TAKEOFF") != 1:
        issues.append(_issue(("steps",), "A MissionSpec must contain exactly one TAKEOFF step."))
    terminal_indices = [index for index, step_type in enumerate(types) if step_type in {"LAND", "RTL"}]
    if len(terminal_indices) != 1:
        issues.append(_issue(("steps",), "A MissionSpec must contain exactly one terminal step."))
    elif terminal_indices[0] != len(steps) - 1:
        issues.append(_issue(("steps", terminal_indices[0], "type"), "The terminal step must be last."))

    mission_altitude = float(constraints["max_altitude_m"])
    mission_radius = float(constraints["max_radius_m"])
    for index, step in enumerate(steps):
        assert isinstance(step, Mapping)
        step_type = step["type"]
        if step_type == "TAKEOFF":
            _check_step_altitude(issues, index, float(step["altitude_m"]), mission_altitude)
        elif step_type == "GOTO_LOCAL":
            north = float(step["north_m"])
            east = float(step["east_m"])
            altitude = -float(step["down_m"])
            if not all(isfinite(value) for value in (north, east, altitude)):
                issues.append(_issue(("steps", index), "Local waypoint values must be finite."))
            elif hypot(north, east) > mission_radius:
                issues.append(
                    _issue(("steps", index), "Local waypoint exceeds the mission radius.")
                )
            elif not _inside_fence(profile, north, east):
                issues.append(
                    _issue(("steps", index), "Local waypoint is outside the allowed geofence.")
                )
            _check_step_altitude(issues, index, altitude, mission_altitude)
        elif step_type == "SURVEY_AREA":
            _check_survey_step(issues, index, step, mission_radius, mission_altitude, profile)

    return tuple(sorted(issues, key=lambda issue: (tuple(map(str, issue.path)), issue.message)))


def _inside_fence(profile: MissionSafetyProfile, north_m: float, east_m: float) -> bool:
    """Whether the platform's fence admits a point. No fence admits everything."""
    return profile.allowed_geofence is None or profile.allowed_geofence.contains(north_m, east_m)


def _check_survey_step(
    issues: list[ValidationIssue],
    index: int,
    step: Mapping[str, Any],
    mission_radius: float,
    mission_altitude: float,
    profile: MissionSafetyProfile,
) -> None:
    """Refuse an area whose far edge, or whose pattern, leaves the envelope.

    The radius check is on the *reach*, not the centre: a 30 m sweep centred
    40 m out would otherwise pass a 50 m limit and fly to 70 m.
    """
    centre_north = float(step["centre_north_m"])
    centre_east = float(step["centre_east_m"])
    radius = float(step["radius_m"])
    spacing = float(step["spacing_m"])
    altitude = float(step["altitude_m"])
    if not all(isfinite(value) for value in (centre_north, centre_east, radius, spacing, altitude)):
        issues.append(_issue(("steps", index), "Survey values must be finite."))
        return
    _check_step_altitude(issues, index, altitude, mission_altitude)
    reach = survey_reach_m(centre_north_m=centre_north, centre_east_m=centre_east, radius_m=radius)
    if reach > mission_radius:
        issues.append(
            _issue(("steps", index), "Survey area reaches beyond the mission radius.")
        )
        return
    try:
        waypoints = survey_waypoints(
            centre_north_m=centre_north,
            centre_east_m=centre_east,
            radius_m=radius,
            spacing_m=spacing,
        )
    except SurveyPatternError as error:
        issues.append(_issue(("steps", index), str(error)))
        return
    # Every swept waypoint, not just the reach: a circle can sit inside the
    # radius and still push its corners past a fence that is not a circle.
    if any(not _inside_fence(profile, north, east) for north, east in waypoints):
        issues.append(
            _issue(("steps", index), "Survey area reaches outside the allowed geofence.")
        )


def _check_finite_numbers(issues: list[ValidationIssue], source: Mapping[str, Any]) -> None:
    constraints = source["constraints"]
    steps = source["steps"]
    assert isinstance(constraints, Mapping)
    assert isinstance(steps, list)
    for field in (
        "max_altitude_m",
        "max_speed_m_s",
        "max_radius_m",
        "minimum_battery_percent_to_start",
    ):
        if not isfinite(float(constraints[field])):
            issues.append(_issue(("constraints", field), "Numeric values must be finite."))
    for index, step in enumerate(steps):
        assert isinstance(step, Mapping)
        for field in ("altitude_m", "north_m", "east_m", "down_m", "duration_s"):
            if field in step and not isfinite(float(step[field])):
                issues.append(_issue(("steps", index, field), "Numeric values must be finite."))


def _check_platform_ceiling(
    issues: list[ValidationIssue],
    constraints: Mapping[str, Any],
    field: str,
    platform_limit: float,
    description: str,
) -> None:
    value = float(constraints[field])
    if not isfinite(value) or value > platform_limit:
        issues.append(
            _issue(("constraints", field), f"Mission {field} exceeds the {description}.")
        )


def _check_step_altitude(
    issues: list[ValidationIssue], index: int, altitude_m: float, mission_altitude_m: float
) -> None:
    if not isfinite(altitude_m) or altitude_m > mission_altitude_m:
        issues.append(
            _issue(("steps", index), "Step altitude exceeds the mission altitude limit.")
        )


def _compile(source: Mapping[str, Any], profile: MissionSafetyProfile) -> CompiledMission:
    constraints = source["constraints"]
    steps = source["steps"]
    assert isinstance(constraints, Mapping)
    assert isinstance(steps, list)
    gate = SafetyGate(
        FlightLimits(
            max_altitude_m=float(constraints["max_altitude_m"]),
            max_distance_m=float(constraints["max_radius_m"]),
            # The document may tighten altitude and radius; it cannot state a
            # fence at all, so the platform's own is the only one there is.
            allowed_geofence=profile.allowed_geofence,
        )
    )
    takeoff_altitude_m = float(steps[0]["altitude_m"])
    commands: list[TakeoffCommand | WaypointCommand | ReturnToHomeCommand | LandCommand] = []
    hold_durations: list[float] = []
    for step in steps:
        step_type = step["type"]
        if step_type == "TAKEOFF":
            command = TakeoffCommand(target_altitude_m=float(step["altitude_m"]))
            gate.evaluate(command)
            commands.append(command)
        elif step_type == "GOTO_LOCAL":
            command = WaypointCommand(
                north_m=float(step["north_m"]),
                east_m=float(step["east_m"]),
                target_altitude_m=-float(step["down_m"]),
            )
            gate.evaluate(command)
            commands.append(command)
        elif step_type == "SURVEY_AREA":
            # One reviewable step, many gate-checked commands: the reviewer sees
            # the area, and the gate still sees every waypoint individually.
            for north_m, east_m in survey_waypoints(
                centre_north_m=float(step["centre_north_m"]),
                centre_east_m=float(step["centre_east_m"]),
                radius_m=float(step["radius_m"]),
                spacing_m=float(step["spacing_m"]),
            ):
                command = WaypointCommand(
                    north_m=north_m, east_m=east_m, target_altitude_m=float(step["altitude_m"])
                )
                gate.evaluate(command)
                commands.append(command)
        elif step_type == "HOLD":
            hold_durations.append(float(step["duration_s"]))
        elif step_type == "RTL":
            command = ReturnToHomeCommand(target_altitude_m=takeoff_altitude_m)
            gate.evaluate(command)
            commands.append(command)
        elif step_type == "LAND":
            command = LandCommand()
            gate.evaluate(command)
            commands.append(command)

    canonical_source = json.dumps(source, sort_keys=True, separators=(",", ":"), allow_nan=False)
    abort_policy = source["abort_policy"]
    assert isinstance(abort_policy, Mapping)
    return CompiledMission(
        mission_id=str(source["mission_id"]),
        vehicle_id=profile.vehicle_id,
        source_hash=sha256(canonical_source.encode()).hexdigest(),
        commands=tuple(commands),
        hold_durations_s=tuple(hold_durations),
        terminal_action=str(steps[-1]["type"]),
        abort_policy=tuple(sorted((str(key), str(value)) for key, value in abort_policy.items())),
    )
