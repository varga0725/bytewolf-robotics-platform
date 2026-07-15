"""Deterministic validation and compilation for the MissionSpec v0.1 boundary."""

from dataclasses import dataclass
from hashlib import sha256
import json
from math import hypot, isfinite
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from brain.mission.commands import LandCommand, ReturnToHomeCommand, TakeoffCommand, WaypointCommand
from brain.safety.gate import FlightLimits, SafetyGate


_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "interfaces/mission_spec/mission_spec_v0_1.schema.json"
)
_SCHEMA = json.loads(_SCHEMA_PATH.read_text())
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


@dataclass(frozen=True)
class MissionSafetyProfile:
    """Hard platform limits that a MissionSpec may tighten but never relax."""

    vehicle_id: str
    max_altitude_m: float
    max_speed_m_s: float
    max_radius_m: float
    minimum_battery_percent_to_start: float
    loss_of_link_action: str


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
    schema_issues = tuple(
        ValidationIssue(tuple(error.absolute_path), error.message)
        for error in sorted(_VALIDATOR.iter_errors(source), key=_schema_error_sort_key)
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
            _check_step_altitude(issues, index, altitude, mission_altitude)

    return tuple(sorted(issues, key=lambda issue: (tuple(map(str, issue.path)), issue.message)))


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
