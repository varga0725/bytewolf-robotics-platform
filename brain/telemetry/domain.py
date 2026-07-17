"""ROS-independent, telemetry-only events derived from the versioned bridge contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from math import isfinite
from typing import Any

from brain.telemetry.ros2_contract import load_ros2_telemetry_bridge_contract


class TelemetryContractError(ValueError):
    """Raised when a sample cannot safely be published through the telemetry contract."""


@dataclass(frozen=True)
class PositionTelemetryEvent:
    """Validated MAVSDK global position sample for a declared telemetry topic."""

    topic: str
    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float
    relative_altitude_m: float
    observed_at: datetime


@dataclass(frozen=True)
class BatteryTelemetryEvent:
    """Validated battery sample for a declared telemetry topic."""

    topic: str
    remaining_percent: float
    observed_at: datetime


@dataclass(frozen=True)
class FlightStateTelemetryEvent:
    """Validated in-air state for a declared telemetry topic."""

    topic: str
    in_air: bool
    observed_at: datetime


TelemetryEvent = PositionTelemetryEvent | BatteryTelemetryEvent | FlightStateTelemetryEvent


def route_mavsdk_telemetry(
    source: str, sample: object, *, observed_at: datetime | None = None
) -> TelemetryEvent:
    """Map one MAVSDK sample to exactly one declared, telemetry-only contract topic."""
    topic = _topic_for_source(source)
    timestamp = _timestamp(observed_at)
    if source == "MAVSDK telemetry.position":
        return _position_event(topic, sample, timestamp)
    if source == "MAVSDK telemetry.battery":
        return _battery_event(topic, sample, timestamp)
    if source == "MAVSDK telemetry.in_air":
        return _flight_state_event(topic, sample, timestamp)
    raise TelemetryContractError(f"Telemetry source {source!r} is unknown or undeclared.")


def _topic_for_source(source: str) -> str:
    for topic in load_ros2_telemetry_bridge_contract().topics:
        if topic.source == source:
            return topic.name
    raise TelemetryContractError(f"Telemetry source {source!r} is unknown or undeclared.")


def _position_event(topic: str, sample: object, observed_at: datetime) -> PositionTelemetryEvent:
    latitude = _finite_attribute(sample, "latitude_deg")
    longitude = _finite_attribute(sample, "longitude_deg")
    if not -90.0 <= latitude <= 90.0:
        raise TelemetryContractError("Telemetry field 'latitude_deg' must be between -90.0 and 90.0.")
    if not -180.0 <= longitude <= 180.0:
        raise TelemetryContractError("Telemetry field 'longitude_deg' must be between -180.0 and 180.0.")
    return PositionTelemetryEvent(
        topic=topic,
        latitude_deg=latitude,
        longitude_deg=longitude,
        absolute_altitude_m=_finite_attribute(sample, "absolute_altitude_m"),
        relative_altitude_m=_finite_attribute(sample, "relative_altitude_m"),
        observed_at=observed_at,
    )


def _battery_event(topic: str, sample: object, observed_at: datetime) -> BatteryTelemetryEvent:
    remaining_percent = _finite_attribute(sample, "remaining_percent")
    if not 0.0 <= remaining_percent <= 100.0:
        raise TelemetryContractError(
            "Telemetry field 'remaining_percent' must be between 0.0 and 100.0."
        )
    return BatteryTelemetryEvent(topic, remaining_percent, observed_at)


def _flight_state_event(topic: str, sample: object, observed_at: datetime) -> FlightStateTelemetryEvent:
    if type(sample) is not bool:
        raise TelemetryContractError("Telemetry flight-state sample must be a boolean.")
    return FlightStateTelemetryEvent(topic, sample, observed_at)


def _finite_attribute(sample: object, name: str) -> float:
    value: Any = getattr(sample, name, None)
    if type(value) not in (int, float):
        raise TelemetryContractError(f"Telemetry field {name!r} must be a finite number.")
    converted = float(value)
    if not isfinite(converted):
        raise TelemetryContractError(f"Telemetry field {name!r} must be finite.")
    return converted


def _timestamp(observed_at: datetime | None) -> datetime:
    timestamp = observed_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise TelemetryContractError("Telemetry timestamp must be timezone-aware.")
    return timestamp.astimezone(UTC)
