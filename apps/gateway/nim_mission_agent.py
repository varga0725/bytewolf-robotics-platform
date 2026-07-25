"""Ask NVIDIA NIM for a MissionSpec proposal, never a flight command.

This is intentionally an application-boundary client. It knows how to call the
hosted NIM API and to fail closed when its response is malformed, but it has no
MAVSDK, ROS, or flight-adapter dependency. The deterministic MissionSpec
validator and executable-shape check remain the authorization boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from brain.mission_spec.orchestrator import MissionSpecExecutionError, require_executable_mission
from brain.mission_spec.validation import (
    CompiledMission,
    MissionSafetyProfile,
    validate_and_compile_mission_spec,
)


DEFAULT_NIM_BASE_URL = "https://integrate.api.nvidia.com/v1"
_TIMEOUT_S = 30.0
_MAX_RESPONSE_BYTES = 128 * 1024


class NIMMissionAgentError(RuntimeError):
    """Raised for configuration or transport failures before any mission exists."""


@dataclass(frozen=True)
class MissionAgentRequest:
    """One user request interpreted against one immutable safety profile."""

    text: str
    profile: MissionSafetyProfile


@dataclass(frozen=True)
class MissionAgentRejection:
    """A fail-closed explanation suitable for a CLI or HTTP client."""

    reason: str
    constraint: str | None = None


@dataclass(frozen=True)
class MissionAgentResult:
    """A validated executable mission, or an explanation with no mission."""

    accepted: bool
    model: str
    mission_spec: dict[str, Any] | None
    mission: CompiledMission | None
    rejections: tuple[MissionAgentRejection, ...]


PostJson = Callable[[str, dict[str, str], dict[str, object], float], object]


class NIMMissionAgent:
    """NVIDIA NIM-backed proposal agent constrained to today’s proven routes."""

    def __init__(
        self,
        api_key: str,
        model: str,
        *,
        base_url: str = DEFAULT_NIM_BASE_URL,
        post_json: PostJson | None = None,
    ) -> None:
        if not api_key.strip():
            raise NIMMissionAgentError("NVIDIA_API_KEY must be configured.")
        if not model.strip():
            raise NIMMissionAgentError("NIM_MISSION_MODEL must be configured.")
        parsed_url = urlparse(base_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise NIMMissionAgentError("NIM_BASE_URL must be an absolute https URL.")
        self._api_key = api_key
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._post_json = post_json or _post_json

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> NIMMissionAgent:
        values = os.environ if environment is None else environment
        api_key = values.get("NVIDIA_API_KEY", "")
        model = values.get("NIM_MISSION_MODEL", "")
        return cls(api_key, model, base_url=values.get("NIM_BASE_URL", DEFAULT_NIM_BASE_URL))

    def propose(self, request: MissionAgentRequest) -> MissionAgentResult:
        if not request.text.strip():
            return _rejected(self._model, "The command is empty.", "command.text")
        try:
            response = self._post_json(
                f"{self._base_url}/chat/completions",
                {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                _request_payload(self._model, request),
                _TIMEOUT_S,
            )
        except NIMMissionAgentError:
            raise
        except Exception as error:
            raise NIMMissionAgentError(f"NIM request failed: {type(error).__name__}: {error}") from error

        try:
            envelope = _model_envelope(response)
        except ValueError as error:
            return _rejected(self._model, str(error), "nim.response")
        if envelope.get("kind") != "mission_proposal":
            return _rejected(
                self._model,
                "The NIM agent did not return a mission proposal.",
                "nim.response.kind",
            )
        document = envelope.get("mission_spec")
        if not isinstance(document, dict):
            return _rejected(
                self._model,
                "The NIM agent response does not contain a MissionSpec object.",
                "nim.response.mission_spec",
            )

        document = _bind_platform_policy(document, request.profile)
        report = validate_and_compile_mission_spec(document, request.profile)
        if not report.approved or report.mission is None:
            return MissionAgentResult(
                accepted=False,
                model=self._model,
                mission_spec=document,
                mission=None,
                rejections=tuple(
                    MissionAgentRejection(issue.message, "/".join(map(str, issue.path)) or "MissionSpec")
                    for issue in report.issues
                ),
            )
        try:
            require_executable_mission(report.mission)
        except MissionSpecExecutionError as error:
            return MissionAgentResult(
                accepted=False,
                model=self._model,
                mission_spec=document,
                mission=None,
                rejections=(MissionAgentRejection(str(error), "executable_shape"),),
            )
        return MissionAgentResult(True, self._model, document, report.mission, ())


def _request_payload(model: str, request: MissionAgentRequest) -> dict[str, object]:
    profile = request.profile
    system = "\n".join(
        (
            "You are ByteWolf Mission Agent. You only propose one MissionSpec JSON document.",
            "Never output MAVLink, PX4, ROS, actuator, motor, shell, or tool commands.",
            "Call propose_mission_spec exactly once. Do not emit prose or a second proposal.",
            "Use exactly one of these executable step shapes:",
            "1) TAKEOFF, HOLD, LAND; 2) TAKEOFF, one to four GOTO_LOCAL steps, HOLD, LAND; 3) TAKEOFF, HOLD, RTL.",
            "Use exactly one positive HOLD. If the user omits a hold duration, use 3 seconds.",
            "For a request such as Hungarian 'járőrözz egy 10 méteres négyzeten', infer a closed square patrol yourself: four launch-relative GOTO_LOCAL corners (10,0), (10,10), (0,10), (0,0), at the requested altitude. Do not ask the user to spell out those corners. For other ambiguous destinations, do not invent a waypoint.",
            "For GOTO_LOCAL, down_m must be strictly negative and keep the TAKEOFF altitude: use down_m = -altitude_m, never 0.",
            "Interpret Hungarian 'előre' / English 'forward' as positive north; 'jobbra' / 'right' as positive east.",
            f"Limits: altitude <= {profile.max_altitude_m:g}m; radius <= {profile.max_radius_m:g}m.",
            "Only propose intent and steps in the tool argument. The gateway, not you, supplies mission ID, vehicle ID, constraints, and abort policy.",
        )
    )
    return {
        "model": model,
        "temperature": 0,
        "max_tokens": 1024,
        # This reasoning model otherwise spends the bounded response entirely
        # on an internal trace before it reaches the required tool call.
        "reasoning_budget": 0,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": request.text}],
        # Tool selection is a formatting aid only; local parsing and safety checks
        # are authoritative, and a failed tool call creates no mission.
        "tools": [{
            "type": "function",
            "function": {
                "name": "propose_mission_spec",
                "description": "Propose exactly one high-level MissionSpec for the safety kernel to validate.",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["kind", "mission_spec"],
                    "properties": {
                        "kind": {"const": "mission_proposal"},
                        "mission_spec": _proposal_schema(),
                    },
                },
            },
        }],
        "tool_choice": {"type": "function", "function": {"name": "propose_mission_spec"}},
    }


def _proposal_schema() -> dict[str, object]:
    """The agent may select mission intent and steps, never controlled policy fields."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "steps"],
        "properties": {
            "intent": {"type": "string", "enum": ["test_flight", "inspect_area", "patrol"]},
            "steps": {
                "type": "array",
                "minItems": 3,
                "maxItems": 7,
                "items": {
                    "oneOf": [
                        {"type": "object", "additionalProperties": False, "required": ["type", "altitude_m"], "properties": {"type": {"const": "TAKEOFF"}, "altitude_m": {"type": "number", "exclusiveMinimum": 0}}},
                        {"type": "object", "additionalProperties": False, "required": ["type", "north_m", "east_m", "down_m"], "properties": {"type": {"const": "GOTO_LOCAL"}, "north_m": {"type": "number"}, "east_m": {"type": "number"}, "down_m": {"type": "number", "exclusiveMaximum": 0}}},
                        {"type": "object", "additionalProperties": False, "required": ["type", "duration_s"], "properties": {"type": {"const": "HOLD"}, "duration_s": {"type": "number", "exclusiveMinimum": 0}}},
                        {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {"type": {"const": "LAND"}}},
                        {"type": "object", "additionalProperties": False, "required": ["type"], "properties": {"type": {"const": "RTL"}}},
                    ]
                },
            },
        },
    }


def _bind_platform_policy(
    proposed: Mapping[str, Any], profile: MissionSafetyProfile
) -> dict[str, Any]:
    """Replace every safety-controlled value with a server-owned immutable copy.

    The model may choose an intent and steps, but it cannot choose the vehicle,
    audit identity, platform limits, link-loss behaviour, or abort policy.
    """
    return {
        **proposed,
        "schema_version": "0.1",
        "mission_id": str(uuid4()),
        "vehicle_id": profile.vehicle_id,
        "constraints": {
            "max_altitude_m": profile.max_altitude_m,
            "max_speed_m_s": profile.max_speed_m_s,
            "max_radius_m": profile.max_radius_m,
            "minimum_battery_percent_to_start": profile.minimum_battery_percent_to_start,
            "loss_of_link_action": profile.loss_of_link_action,
        },
        "abort_policy": {
            "on_timeout": "LAND",
            "on_low_battery": profile.loss_of_link_action,
            "on_position_invalid": "LAND",
        },
    }


def _model_envelope(response: object) -> dict[str, Any]:
    if not isinstance(response, Mapping):
        raise ValueError("The NIM response is not a JSON object.")
    try:
        choices = response["choices"]
        choice = choices[0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("The NIM response has no assistant message.") from error
    if not isinstance(message, Mapping):
        raise ValueError("The NIM response has no assistant message.")
    parsed = _tool_arguments(message)
    if parsed is None:
        content = message.get("content")
        if not isinstance(content, str):
            raise ValueError("The NIM assistant message is not a mission tool call or text JSON.")
        parsed = _parse_json_content(content)
    if not isinstance(parsed, dict):
        raise ValueError("The NIM assistant JSON must be an object.")
    return parsed


def _tool_arguments(message: Mapping[str, Any]) -> object | None:
    tool_calls = message.get("tool_calls")
    if tool_calls is None:
        return None
    if not isinstance(tool_calls, list) or len(tool_calls) != 1:
        raise ValueError("The NIM assistant must make exactly one mission proposal tool call.")
    call = tool_calls[0]
    if not isinstance(call, Mapping):
        raise ValueError("The NIM tool call is malformed.")
    function = call.get("function")
    if not isinstance(function, Mapping) or function.get("name") != "propose_mission_spec":
        raise ValueError("The NIM assistant called an unsupported tool.")
    arguments = function.get("arguments")
    if not isinstance(arguments, str):
        raise ValueError("The NIM mission proposal tool arguments are not JSON text.")
    return _parse_json_content(arguments)


def _parse_json_content(content: str) -> object:
    """Accept a JSON object wrapped in an accidental Markdown fence, not prose.

    Some hosted models add a ```json fence despite JSON-mode prompting. We strip
    precisely that presentation layer; prose or multiple JSON objects remains a
    refusal so no ambiguous output can reach the mission validator.
    """
    candidate = content.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[len("```json") : -len("```")].strip()
    elif candidate.startswith("```") and candidate.endswith("```"):
        candidate = candidate[len("```") : -len("```")].strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as error:
        raise ValueError("The NIM assistant message is not valid JSON.") from error


def _rejected(model: str, reason: str, constraint: str | None) -> MissionAgentResult:
    return MissionAgentResult(False, model, None, None, (MissionAgentRejection(reason, constraint),))


def _post_json(url: str, headers: dict[str, str], payload: dict[str, object], timeout_s: float) -> object:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    request = Request(url, data=body, headers=headers, method="POST")
    try:
        with urlopen(request, timeout=timeout_s) as response:
            raw = response.read(_MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        raise NIMMissionAgentError(f"NIM rejected the request with HTTP {error.code}.") from error
    except URLError as error:
        raise NIMMissionAgentError("NIM is unavailable.") from error
    if len(raw) > _MAX_RESPONSE_BYTES:
        raise NIMMissionAgentError("NIM response exceeds the safety limit.")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as error:
        raise NIMMissionAgentError("NIM returned invalid JSON.") from error
