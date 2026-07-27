"""Immutable loaders for the active vehicle safety contract."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import yaml

from brain.safety.gate import FlightLimits, LocalPolygonGeofence


DEFAULT_SAFETY_PROFILE_PATH = (
    Path(__file__).resolve().parents[2] / "shared/config/x500v2/twin.yaml"
)


class SafetyProfileError(ValueError):
    """Raised when a vehicle safety profile is missing or unsafe to use."""


@dataclass(frozen=True)
class OffboardLimits:
    """What a streamed control setpoint may ask for, and for how long.

    Defined here rather than in ``brain/control`` on purpose: these are twin
    limits, they come from the same file as every other limit, and safety must
    not import control. ``max_speed_m_s`` is absent because the profile already
    carries it -- the boundary reads the vehicle's one speed limit rather than a
    second copy that could drift looser than it.
    """

    enabled: bool
    frame: str
    max_setpoint_ttl_s: float
    max_rate_hz: float
    max_acceleration_m_s2: float
    max_yaw_rate_deg_s: float
    max_uncertainty_m_s: float
    watchdog_timeout_s: float
    fallback_sequence: tuple[str, ...]


@dataclass(frozen=True)
class ShieldLimits:
    """What the runtime shield needs to decide whether a velocity is safe.

    Geometry and timing only. There is deliberately no dynamics model here,
    because the twin has no measured dynamics to build one from -- the braking
    figure below is a conservative stand-in, not a capability.
    """

    enabled: bool
    minimum_clearance_m: float
    braking_deceleration_m_s2: float
    reaction_latency_s: float
    max_observation_age_s: float
    unobserved_is_blocked: bool


@dataclass(frozen=True)
class SafetyProfile:
    """The non-overridable safety values of one active vehicle twin."""

    vehicle_id: str
    max_altitude_m: float
    max_speed_m_s: float
    max_radius_m: float
    minimum_battery_percent_to_start: float
    loss_of_link_action: str
    allow_missing_battery_telemetry: bool = False
    allowed_geofence: LocalPolygonGeofence | None = None
    offboard: OffboardLimits | None = None
    shield: ShieldLimits | None = None

    def flight_limits(self) -> FlightLimits:
        return FlightLimits(
            max_altitude_m=self.max_altitude_m,
            max_distance_m=self.max_radius_m,
            allowed_geofence=self.allowed_geofence,
        )


def load_safety_profile(path: Path | str = DEFAULT_SAFETY_PROFILE_PATH) -> SafetyProfile:
    """Load and validate a versioned twin YAML file without exposing mutable state."""
    profile_path = Path(path)
    try:
        source = yaml.safe_load(profile_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise SafetyProfileError(f"Cannot read safety profile '{profile_path}': {error.strerror}.") from error
    except yaml.YAMLError as error:
        raise SafetyProfileError(f"Safety profile '{profile_path}' is not valid YAML.") from error

    if not isinstance(source, Mapping):
        raise SafetyProfileError("Safety profile root must be a mapping.")
    vehicle = _required_mapping(source, "vehicle")
    safety = _required_mapping(source, "safety")
    simulation_value = source.get("simulation", {})
    if not isinstance(simulation_value, Mapping):
        raise SafetyProfileError("Safety profile field 'simulation' must be a mapping.")
    simulation = simulation_value
    return SafetyProfile(
        vehicle_id=_required_string(vehicle, "id"),
        max_altitude_m=_required_positive_number(safety, "max_altitude_m"),
        max_speed_m_s=_required_positive_number(safety, "max_speed_m_s"),
        max_radius_m=_required_positive_number(safety, "max_radius_m"),
        minimum_battery_percent_to_start=_required_percent(
            safety, "minimum_battery_percent_to_start"
        ),
        loss_of_link_action=_required_string(safety, "loss_of_link_action"),
        allow_missing_battery_telemetry=_optional_boolean(
            simulation, "allow_missing_battery_telemetry", default=False
        ),
        allowed_geofence=_optional_geofence(safety),
        offboard=_optional_offboard(safety),
        shield=_optional_shield(safety),
    )


def _optional_shield(source: Mapping[str, Any]) -> ShieldLimits | None:
    """Read the shield limits, or none at all if the twin declares no shield.

    Every field is required for the same reason the offboard block's are: a
    missing clearance or braking figure would have to fall back to a default
    invented in code, and a safety margin nobody wrote down is not one anybody
    can review.
    """
    value = source.get("shield")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SafetyProfileError("Safety profile field 'shield' must be a mapping.")
    return ShieldLimits(
        enabled=_optional_boolean(value, "enabled", default=False),
        minimum_clearance_m=_required_positive_number(value, "minimum_clearance_m"),
        braking_deceleration_m_s2=_required_positive_number(value, "braking_deceleration_m_s2"),
        reaction_latency_s=_required_positive_number(value, "reaction_latency_s"),
        max_observation_age_s=_required_positive_number(value, "max_observation_age_s"),
        unobserved_is_blocked=_optional_boolean(value, "unobserved_is_blocked", default=True),
    )


_FALLBACK_STEPS = frozenset({"zero_velocity", "hold", "land", "rtl"})


def _optional_offboard(source: Mapping[str, Any]) -> OffboardLimits | None:
    """Read the Offboard limits, or none at all if the twin declares no boundary.

    A twin without this block simply has no Offboard path, which is the safe
    reading. What is *not* tolerated is a half-written block: every limit below
    is required, because a missing one would have to fall back to a default, and
    a default speed or timeout invented here is exactly the second source of
    truth this project forbids.
    """
    value = source.get("offboard")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SafetyProfileError("Safety profile field 'offboard' must be a mapping.")

    frame = _required_string(value, "frame")
    if frame not in {"local_ned", "body_frd"}:
        raise SafetyProfileError(
            f"Safety profile field 'offboard.frame' must be local_ned or body_frd, not '{frame}'."
        )
    limits = OffboardLimits(
        enabled=_optional_boolean(value, "enabled", default=False),
        frame=frame,
        max_setpoint_ttl_s=_required_positive_number(value, "max_setpoint_ttl_s"),
        max_rate_hz=_required_positive_number(value, "max_rate_hz"),
        max_acceleration_m_s2=_required_positive_number(value, "max_acceleration_m_s2"),
        max_yaw_rate_deg_s=_required_positive_number(value, "max_yaw_rate_deg_s"),
        max_uncertainty_m_s=_required_positive_number(value, "max_uncertainty_m_s"),
        watchdog_timeout_s=_required_positive_number(value, "watchdog_timeout_s"),
        fallback_sequence=_required_fallback_sequence(value),
    )
    if limits.watchdog_timeout_s <= limits.max_setpoint_ttl_s:
        # Otherwise the stream is declared dead before its last setpoint has even
        # expired, and the vehicle would still be acting on a command the
        # watchdog has already given up on.
        raise SafetyProfileError(
            "Safety profile field 'offboard.watchdog_timeout_s' must exceed 'max_setpoint_ttl_s'."
        )
    return limits


def _required_fallback_sequence(source: Mapping[str, Any]) -> tuple[str, ...]:
    value = source.get("fallback_sequence")
    if not isinstance(value, list) or not value:
        raise SafetyProfileError(
            "Safety profile field 'offboard.fallback_sequence' must be a non-empty list."
        )
    steps = tuple(str(step) for step in value)
    unknown = [step for step in steps if step not in _FALLBACK_STEPS]
    if unknown:
        raise SafetyProfileError(
            f"Safety profile field 'offboard.fallback_sequence' has unknown steps: {unknown}."
        )
    if steps[0] != "zero_velocity":
        # Anything else means the vehicle keeps its last commanded velocity for
        # one more step while the fallback decides what to do.
        raise SafetyProfileError(
            "Safety profile field 'offboard.fallback_sequence' must begin with 'zero_velocity'."
        )
    if len(set(steps)) != len(steps):
        raise SafetyProfileError(
            "Safety profile field 'offboard.fallback_sequence' must not repeat a step."
        )
    return steps


def _required_mapping(source: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = source.get(field)
    if not isinstance(value, Mapping):
        raise SafetyProfileError(f"Safety profile field '{field}' must be a mapping.")
    return value


def _required_string(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise SafetyProfileError(f"Safety profile field '{field}' must be a non-empty string.")
    return value


def _required_positive_number(source: Mapping[str, Any], field: str) -> float:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SafetyProfileError(f"Safety profile field '{field}' must be a finite positive number.")
    number = float(value)
    if not isfinite(number) or number <= 0.0:
        raise SafetyProfileError(f"Safety profile field '{field}' must be a finite positive number.")
    return number


def _required_percent(source: Mapping[str, Any], field: str) -> float:
    value = _required_positive_number(source, field)
    if value > 100.0:
        raise SafetyProfileError(f"Safety profile field '{field}' must not exceed 100.")
    return value


def _optional_boolean(source: Mapping[str, Any], field: str, default: bool) -> bool:
    value = source.get(field, default)
    if not isinstance(value, bool):
        raise SafetyProfileError(f"Safety profile field '{field}' must be a boolean.")
    return value


def _optional_geofence(source: Mapping[str, Any]) -> LocalPolygonGeofence | None:
    value = source.get("allowed_geofence")
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise SafetyProfileError("Safety profile field 'allowed_geofence' must be a mapping.")
    vertices = value.get("vertices_m")
    if not isinstance(vertices, list):
        raise SafetyProfileError("Safety profile geofence field 'vertices_m' must be a list.")
    try:
        normalized = tuple(_geofence_vertex(vertex) for vertex in vertices)
        return LocalPolygonGeofence(vertices_m=normalized)
    except (TypeError, ValueError) as error:
        raise SafetyProfileError(f"Safety profile geofence is invalid: {error}") from error


def _geofence_vertex(value: Any) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError("Each geofence vertex must be a two-value [north_m, east_m] list.")
    north_m, east_m = value
    if isinstance(north_m, bool) or isinstance(east_m, bool):
        raise ValueError("Geofence coordinates must be finite numbers.")
    if not isinstance(north_m, (int, float)) or not isinstance(east_m, (int, float)):
        raise ValueError("Geofence coordinates must be finite numbers.")
    if not isfinite(float(north_m)) or not isfinite(float(east_m)):
        raise ValueError("Geofence coordinates must be finite numbers.")
    return float(north_m), float(east_m)
