"""Turn a bounded natural-language request into a validated MissionSpec.

The gateway is the front door for a spoken or typed mission, and it is built so
that the language never reaches the vehicle unchecked. It only ever produces a
MissionSpec document and hands it to the existing validator, compiler, and
SafetyGate; it opens no PX4 connection and emits no MAVLink, actuator, or motor
command. Whatever the words say, the deterministic safety layer is still the
authority that approves or refuses the mission.

The parse is deterministic, not a language model: a bounded grammar of V1
intents (take off, go to a local point, hold, return, land) in English and in
the canonical Hungarian demo phrasing. Anything outside that grammar is refused
with a structured reason that names the exact clause, and anything the grammar
accepts is still only a proposal until the validator approves it -- so an
over-altitude "take off to 500 m" parses cleanly and is then rejected by the
platform ceiling, with the offending text and the failed constraint both named.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from brain.mission_spec.validation import (
    CompiledMission,
    MissionSafetyProfile,
    validate_and_compile_mission_spec,
)


# A stable namespace so the same request always yields the same mission_id; the
# spec must be deterministic, so the id cannot come from a clock or randomness.
_MISSION_ID_NAMESPACE = uuid5(NAMESPACE_URL, "bytewolf.mission_spec.command_gateway")

_CLAUSE_SEPARATORS = re.compile(r"\s*(?:,|;|\bthen\b|\band\b|\bmajd\b|\bés\b)\s*", re.IGNORECASE)
_NUMBER = r"(\d+(?:\.\d+)?)"

_TAKEOFF = re.compile(rf"(?:take\s*off|takeoff|sz[aá]llj\s+fel)\D*{_NUMBER}\s*(?:m|met[er]+s?|m[eé]ter)", re.IGNORECASE)
_HOLD = re.compile(rf"(?:hover|hold|wait|lebeg[jn]?)\D*{_NUMBER}\s*(?:s\b|sec|second|m[aá]sodperc)", re.IGNORECASE)
_GOTO_DIRECTION = re.compile(
    rf"(?:fly|go|move|rep[uü]lj)\D*{_NUMBER}\s*(?:m|met[er]+s?|m[eé]ter)\D*"
    r"(north|south|east|west|[eé]szak|d[eé]l|kelet|nyugat)",
    re.IGNORECASE,
)
_GOTO_DESIGNATED = re.compile(
    r"(?:fly|go|move|rep[uü]lj).*(?:designated\s+point|kijel[oö]lt\s+pont)", re.IGNORECASE
)
_RETURN = re.compile(r"return|come\s+back|go\s+home|\brtl\b|gyere\s+vissza|vissza", re.IGNORECASE)
_LAND = re.compile(r"\bland\b|sz[aá]llj\s+le|leszáll", re.IGNORECASE)
_INSPECT = re.compile(r"inspect|vizsg[aá]l", re.IGNORECASE)

_DIRECTION_OFFSETS = {
    "north": (1.0, 0.0), "észak": (1.0, 0.0),
    "south": (-1.0, 0.0), "dél": (-1.0, 0.0), "del": (-1.0, 0.0),
    "east": (0.0, 1.0), "kelet": (0.0, 1.0),
    "west": (0.0, -1.0), "nyugat": (0.0, -1.0),
}


@dataclass(frozen=True)
class CommandRequest:
    """One bounded natural-language mission request and the context it needs."""

    text: str
    vehicle_id: str
    # The point "fly to the designated point" refers to, as local north/east
    # metres. Absent means the phrase cannot be resolved and must be refused.
    designated_point_m: tuple[float, float] | None = None


@dataclass(frozen=True)
class GatewayRejection:
    """Why a request was refused, in terms a caller can act on."""

    reason: str
    source_text: str
    constraint: str | None = None


@dataclass(frozen=True)
class GatewayResult:
    """The outcome of interpreting a request: an approved mission, or refusals."""

    accepted: bool
    mission: CompiledMission | None
    mission_spec: dict[str, Any] | None
    rejections: tuple[GatewayRejection, ...]


def interpret_command(request: CommandRequest, profile: MissionSafetyProfile) -> GatewayResult:
    """Interpret a request into an approved mission, or a structured refusal.

    The parse can only ever propose; the returned mission is whatever the
    existing validator and SafetyGate approved, and nothing else runs here.
    """
    clauses = _clauses(request.text)
    if not clauses:
        return _refused([GatewayRejection("The request is empty.", request.text)])

    steps: list[dict[str, Any]] = []
    flight_altitude_m = 2.0
    for clause in clauses:
        step, rejection = _step_for_clause(clause, request, flight_altitude_m)
        if rejection is not None:
            return _refused([rejection])
        assert step is not None
        if step["type"] == "TAKEOFF":
            flight_altitude_m = step["altitude_m"]
        steps.append(step)

    steps = _absorb_redundant_land_after_return(steps)
    document = _mission_spec_document(request, profile, steps)
    report = validate_and_compile_mission_spec(document, profile)
    if not report.approved:
        return GatewayResult(
            accepted=False,
            mission=None,
            mission_spec=document,
            rejections=tuple(_rejection_from_issue(issue, request.text) for issue in report.issues),
        )
    return GatewayResult(accepted=True, mission=report.mission, mission_spec=document, rejections=())


def _clauses(text: str) -> list[str]:
    return [clause.strip() for clause in _CLAUSE_SEPARATORS.split(text.strip()) if clause.strip()]


def _step_for_clause(
    clause: str, request: CommandRequest, flight_altitude_m: float
) -> tuple[dict[str, Any] | None, GatewayRejection | None]:
    takeoff = _TAKEOFF.search(clause)
    if takeoff:
        return {"type": "TAKEOFF", "altitude_m": float(takeoff.group(1))}, None

    hold = _HOLD.search(clause)
    if hold:
        return {"type": "HOLD", "duration_s": float(hold.group(1))}, None

    direction = _GOTO_DIRECTION.search(clause)
    if direction:
        north_sign, east_sign = _DIRECTION_OFFSETS[direction.group(2).lower()]
        distance = float(direction.group(1))
        return (
            {
                "type": "GOTO_LOCAL",
                "north_m": north_sign * distance,
                "east_m": east_sign * distance,
                "down_m": -flight_altitude_m,
            },
            None,
        )

    if _GOTO_DESIGNATED.search(clause):
        if request.designated_point_m is None:
            return None, GatewayRejection(
                "The request refers to a designated point, but no coordinate was provided.",
                clause,
                constraint="designated_point",
            )
        north_m, east_m = request.designated_point_m
        return (
            {"type": "GOTO_LOCAL", "north_m": north_m, "east_m": east_m, "down_m": -flight_altitude_m},
            None,
        )

    if _RETURN.search(clause):
        return {"type": "RTL"}, None
    if _LAND.search(clause):
        return {"type": "LAND"}, None

    return None, GatewayRejection(
        "The request contains an instruction the gateway does not support.", clause
    )


def _absorb_redundant_land_after_return(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop a LAND that immediately follows an RTL, since RTL already lands.

    "Come back and land" is one action -- return to launch and land there -- but
    it splits into an RTL clause and a LAND clause. Keeping both would be two
    terminal steps, which the contract forbids, so the redundant trailing LAND
    is folded into the RTL that performs it. A LAND anywhere else is left alone,
    and a contradictory order (LAND then RTL) still reaches the validator.
    """
    if len(steps) >= 2 and steps[-2]["type"] == "RTL" and steps[-1]["type"] == "LAND":
        return steps[:-1]
    return steps


def _mission_spec_document(
    request: CommandRequest, profile: MissionSafetyProfile, steps: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "schema_version": "0.1",
        "mission_id": str(uuid5(_MISSION_ID_NAMESPACE, f"{request.vehicle_id}:{request.text.strip().lower()}")),
        "vehicle_id": request.vehicle_id,
        "intent": "inspect_area" if _INSPECT.search(request.text) else "test_flight",
        "constraints": {
            "max_altitude_m": profile.max_altitude_m,
            "max_speed_m_s": profile.max_speed_m_s,
            "max_radius_m": profile.max_radius_m,
            "minimum_battery_percent_to_start": profile.minimum_battery_percent_to_start,
            "loss_of_link_action": profile.loss_of_link_action,
        },
        "steps": steps,
        "abort_policy": {
            "on_timeout": "LAND",
            "on_low_battery": profile.loss_of_link_action,
            "on_position_invalid": "LAND",
        },
    }


def _rejection_from_issue(issue: Any, source_text: str) -> GatewayRejection:
    path = "/".join(str(part) for part in issue.path) or "<mission>"
    return GatewayRejection(reason=issue.message, source_text=source_text, constraint=path)


def _refused(rejections: list[GatewayRejection]) -> GatewayResult:
    return GatewayResult(accepted=False, mission=None, mission_spec=None, rejections=tuple(rejections))
