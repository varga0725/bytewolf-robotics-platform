"""Immutable, versioned execution-time safety policy."""

from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any, Mapping

import yaml


DEFAULT_RUNTIME_POLICY_PATH = (
    Path(__file__).resolve().parents[2] / "shared/config/x500v2/runtime_policy.v0_1.yaml"
)


class RuntimePolicyError(ValueError):
    """Raised when the runtime policy cannot guarantee the safety contract."""


@dataclass(frozen=True)
class RuntimePolicy:
    """Timeouts and bounded failure handling for one mission execution."""

    version: str
    waypoint_timeout_s: float
    landing_confirmation_timeout_s: float
    fallback_land_attempts: int
    minimum_battery_percent_to_continue: float = 35.0
    telemetry_sample_timeout_s: float = 5.0


def load_runtime_policy(path: Path | str = DEFAULT_RUNTIME_POLICY_PATH) -> RuntimePolicy:
    """Load a validated policy whose single fallback prevents actuation retries."""
    policy_path = Path(path)
    try:
        source = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise RuntimePolicyError(f"Cannot read runtime policy '{policy_path}': {error.strerror}.") from error
    except yaml.YAMLError as error:
        raise RuntimePolicyError(f"Runtime policy '{policy_path}' is not valid YAML.") from error

    if not isinstance(source, Mapping):
        raise RuntimePolicyError("Runtime policy root must be a mapping.")
    timeouts = _required_mapping(source, "timeouts")
    handling = _required_mapping(source, "failure_handling")
    watchdog = _required_mapping(source, "runtime_watchdog")
    fallback_attempts = handling.get("fallback_land_attempts")
    if fallback_attempts != 1:
        raise RuntimePolicyError("Runtime policy field 'fallback_land_attempts' must be exactly 1.")
    return RuntimePolicy(
        version=_required_string(source, "version"),
        waypoint_timeout_s=_required_positive_number(timeouts, "waypoint_s"),
        landing_confirmation_timeout_s=_required_positive_number(
            timeouts, "landing_confirmation_s"
        ),
        fallback_land_attempts=fallback_attempts,
        minimum_battery_percent_to_continue=_required_percent(
            watchdog, "minimum_battery_percent_to_continue"
        ),
        telemetry_sample_timeout_s=_required_positive_number(
            watchdog, "telemetry_sample_timeout_s"
        ),
    )


def _required_mapping(source: Mapping[str, Any], field: str) -> Mapping[str, Any]:
    value = source.get(field)
    if not isinstance(value, Mapping):
        raise RuntimePolicyError(f"Runtime policy field '{field}' must be a mapping.")
    return value


def _required_string(source: Mapping[str, Any], field: str) -> str:
    value = source.get(field)
    if not isinstance(value, str) or not value.strip():
        raise RuntimePolicyError(f"Runtime policy field '{field}' must be a non-empty string.")
    return value


def _required_positive_number(source: Mapping[str, Any], field: str) -> float:
    value = source.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RuntimePolicyError(f"Runtime policy field '{field}' must be a finite positive number.")
    number = float(value)
    if not isfinite(number) or number <= 0.0:
        raise RuntimePolicyError(f"Runtime policy field '{field}' must be a finite positive number.")
    return number


def _required_percent(source: Mapping[str, Any], field: str) -> float:
    value = _required_positive_number(source, field)
    if value > 100.0:
        raise RuntimePolicyError(f"Runtime policy field '{field}' must not exceed 100.")
    return value
