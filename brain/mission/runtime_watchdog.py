"""Immutable runtime telemetry safety decisions for a still-running controller.

The watchdog can request a single landing fallback only while this MAVSDK
process is still executing.  A terminated process cannot command the vehicle;
that case deliberately delegates to the configured PX4 failsafe.
"""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite


class RuntimeSafetyAction(StrEnum):
    """The sole bounded app-side fallback action."""

    LAND = "land"


class RuntimeFaultKind(StrEnum):
    """Runtime telemetry failures with audit-stable names."""

    LOW_BATTERY = "low_battery"
    GNSS_INVALID = "gnss_invalid"
    TELEMETRY_UNAVAILABLE = "telemetry_unavailable"
    MAVSDK_CLIENT_PROCESS_STOPPED = "mavsdk_client_process_stopped"


@dataclass(frozen=True)
class RuntimeFault:
    """A small, immutable explanation of why continued flight is forbidden."""

    kind: RuntimeFaultKind
    source: str


@dataclass(frozen=True)
class RuntimeSafetyDecision:
    """Whether a running mission may continue, and any bounded response."""

    permitted: bool
    action: RuntimeSafetyAction | None
    fault: RuntimeFault | None
    requires_external_failsafe: bool = False


class RuntimeTelemetryWatchdog:
    """Validate battery and GNSS samples without issuing actuator commands."""

    def __init__(self, minimum_battery_percent: float, telemetry_sample_timeout_s: float) -> None:
        if not _finite_percent(minimum_battery_percent):
            raise ValueError("minimum_battery_percent must be a finite value from 0 to 100.")
        if not _finite_positive(telemetry_sample_timeout_s):
            raise ValueError("telemetry_sample_timeout_s must be a finite positive number.")
        self._minimum_battery_percent = minimum_battery_percent
        self._telemetry_sample_timeout_s = telemetry_sample_timeout_s

    @property
    def telemetry_sample_timeout_s(self) -> float:
        return self._telemetry_sample_timeout_s

    def evaluate(self, battery: object, position: object) -> RuntimeSafetyDecision:
        """Return a landing decision for unsafe live samples, otherwise continue."""
        battery_decision = self.evaluate_battery(battery)
        if not battery_decision.permitted:
            return battery_decision
        position_decision = self.evaluate_position(position)
        if not position_decision.permitted:
            return position_decision
        return RuntimeSafetyDecision(permitted=True, action=None, fault=None)

    def evaluate_battery(self, battery: object) -> RuntimeSafetyDecision:
        """Apply the continuation reserve to one live battery sample."""
        battery_percent = _battery_percent(battery)
        if battery_percent is None or battery_percent < self._minimum_battery_percent:
            return self._land(RuntimeFaultKind.LOW_BATTERY, "battery")
        return RuntimeSafetyDecision(permitted=True, action=None, fault=None)

    @staticmethod
    def evaluate_position(position: object) -> RuntimeSafetyDecision:
        """Reject absent, non-finite, or out-of-range GNSS samples in flight."""
        if not _valid_position(position):
            return RuntimeTelemetryWatchdog._land(RuntimeFaultKind.GNSS_INVALID, "position")
        return RuntimeSafetyDecision(permitted=True, action=None, fault=None)

    @staticmethod
    def telemetry_unavailable(source: str) -> RuntimeSafetyDecision:
        return RuntimeTelemetryWatchdog._land(RuntimeFaultKind.TELEMETRY_UNAVAILABLE, source)

    @staticmethod
    def client_process_stopped() -> RuntimeSafetyDecision:
        """Record the hard boundary: dead code cannot send an actuator command."""
        return RuntimeSafetyDecision(
            permitted=False,
            action=None,
            fault=RuntimeFault(RuntimeFaultKind.MAVSDK_CLIENT_PROCESS_STOPPED, "mavsdk-client"),
            requires_external_failsafe=True,
        )

    @staticmethod
    def _land(kind: RuntimeFaultKind, source: str) -> RuntimeSafetyDecision:
        return RuntimeSafetyDecision(
            permitted=False,
            action=RuntimeSafetyAction.LAND,
            fault=RuntimeFault(kind, source),
        )


def _battery_percent(battery: object) -> float | None:
    """Read MAVSDK's battery percentage, which is already a 0-100 value."""
    try:
        remaining = float(getattr(battery, "remaining_percent"))
    except (AttributeError, TypeError, ValueError):
        return None
    if not isfinite(remaining) or not 0.0 <= remaining <= 100.0:
        return None
    return remaining


def _valid_position(position: object) -> bool:
    try:
        values = (
            float(getattr(position, "latitude_deg")),
            float(getattr(position, "longitude_deg")),
            float(getattr(position, "absolute_altitude_m")),
        )
    except (AttributeError, TypeError, ValueError):
        return False
    latitude, longitude, _altitude = values
    return all(isfinite(value) for value in values) and -90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0


def _finite_percent(value: float) -> bool:
    return isfinite(value) and 0.0 <= value <= 100.0


def _finite_positive(value: float) -> bool:
    return isfinite(value) and value > 0.0
