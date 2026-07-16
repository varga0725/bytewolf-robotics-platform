"""Immutable loaders for the active vehicle safety contract."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import yaml

from brain.safety.gate import FlightLimits


DEFAULT_SAFETY_PROFILE_PATH = (
    Path(__file__).resolve().parents[2] / "platforms/x500v2/config/twin.yaml"
)


class SafetyProfileError(ValueError):
    """Raised when a vehicle safety profile is missing or unsafe to use."""


@dataclass(frozen=True)
class SafetyProfile:
    """The non-overridable safety values of one active vehicle twin."""

    vehicle_id: str
    max_altitude_m: float
    max_speed_m_s: float
    max_radius_m: float
    minimum_battery_percent_to_start: float
    loss_of_link_action: str

    def flight_limits(self) -> FlightLimits:
        return FlightLimits(
            max_altitude_m=self.max_altitude_m,
            max_distance_m=self.max_radius_m,
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
    return SafetyProfile(
        vehicle_id=_required_string(vehicle, "id"),
        max_altitude_m=_required_positive_number(safety, "max_altitude_m"),
        max_speed_m_s=_required_positive_number(safety, "max_speed_m_s"),
        max_radius_m=_required_positive_number(safety, "max_radius_m"),
        minimum_battery_percent_to_start=_required_percent(
            safety, "minimum_battery_percent_to_start"
        ),
        loss_of_link_action=_required_string(safety, "loss_of_link_action"),
    )


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
