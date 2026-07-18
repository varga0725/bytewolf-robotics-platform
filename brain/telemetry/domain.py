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


@dataclass(frozen=True)
class SupplementalTelemetryEvent:
    """Validated read-only vehicle state beyond the dashboard's three core streams."""

    topic: str
    source: str
    payload: tuple[tuple[str, bool | float | int | str], ...]
    observed_at: datetime


TelemetryEvent = (
    PositionTelemetryEvent | BatteryTelemetryEvent | FlightStateTelemetryEvent | SupplementalTelemetryEvent
)


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
    if source in _SUPPLEMENTAL_FIELDS:
        return _supplemental_event(topic, source, sample, timestamp)
    raise TelemetryContractError(f"Telemetry source {source!r} is unknown or undeclared.")


def _topic_for_source(source: str) -> str:
    for topic in load_ros2_telemetry_bridge_contract().topics:
        if topic.source == source:
            return topic.name
    if source in _SUPPLEMENTAL_FIELDS:
        return f"telemetry/history/{source.removeprefix('MAVSDK telemetry.')}"
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


_SUPPLEMENTAL_FIELDS: dict[str, tuple[tuple[str, str], ...]] = {
    "MAVSDK telemetry.velocity_ned": (("north_m_s", "finite"), ("east_m_s", "finite"), ("down_m_s", "finite")),
    "MAVSDK telemetry.attitude_euler": (("roll_deg", "finite"), ("pitch_deg", "finite"), ("yaw_deg", "finite")),
    "MAVSDK telemetry.gps_info": (("num_satellites", "integer"), ("fix_type", "string")),
    "MAVSDK telemetry.flight_mode": (("value", "string"),),
    "MAVSDK telemetry.armed": (("value", "boolean"),),
    "MAVSDK telemetry.landed_state": (("value", "string"),),
    "MAVSDK telemetry.health": (
        ("is_global_position_ok", "boolean"),
        ("is_home_position_ok", "boolean"),
        ("is_local_position_ok", "boolean"),
    ),
    "MAVSDK telemetry.imu": (),
    "MAVSDK telemetry.battery_diagnostics": (),
    "MAVSDK telemetry.ground_truth": (),
    "MAVSDK telemetry.position_velocity_ned": (),
}


def _supplemental_event(
    topic: str, source: str, sample: object, observed_at: datetime
) -> SupplementalTelemetryEvent:
    if source == "MAVSDK telemetry.imu":
        return _imu_event(topic, source, sample, observed_at)
    if source == "MAVSDK telemetry.battery_diagnostics":
        return _battery_diagnostics_event(topic, source, sample, observed_at)
    if source == "MAVSDK telemetry.ground_truth":
        return _ground_truth_event(topic, source, sample, observed_at)
    if source == "MAVSDK telemetry.position_velocity_ned":
        return _position_velocity_ned_event(topic, source, sample, observed_at)
    values: list[tuple[str, bool | float | int | str]] = []
    for field, kind in _SUPPLEMENTAL_FIELDS[source]:
        raw = getattr(sample, field, sample if field == "value" else None)
        if kind == "finite":
            values.append((field, _finite_value(raw, field)))
        elif kind == "integer":
            if type(raw) is not int or raw < 0:
                raise TelemetryContractError(f"Telemetry field {field!r} must be a non-negative integer.")
            values.append((field, raw))
        elif kind == "boolean":
            if type(raw) is not bool:
                raise TelemetryContractError(f"Telemetry field {field!r} must be a boolean.")
            values.append((field, raw))
        else:
            value = getattr(raw, "value", raw)
            if not isinstance(value, str) or not value:
                raise TelemetryContractError(f"Telemetry field {field!r} must be a non-empty string.")
            values.append((field, value))
    return SupplementalTelemetryEvent(topic, source, tuple(values), observed_at)


def _imu_event(
    topic: str, source: str, sample: object, observed_at: datetime
) -> SupplementalTelemetryEvent:
    """Store the X500 IMU's acceleration, angular velocity and magnetic field in FRD."""
    fields = (
        ("acceleration_frd", "forward_m_s2"),
        ("acceleration_frd", "right_m_s2"),
        ("acceleration_frd", "down_m_s2"),
        ("angular_velocity_frd", "forward_rad_s"),
        ("angular_velocity_frd", "right_rad_s"),
        ("angular_velocity_frd", "down_rad_s"),
        ("magnetic_field_frd", "forward_gauss"),
        ("magnetic_field_frd", "right_gauss"),
        ("magnetic_field_frd", "down_gauss"),
        ("", "temperature_degc"),
    )
    values: list[tuple[str, bool | float | int | str]] = []
    for parent, field in fields:
        container = getattr(sample, parent, sample) if parent else sample
        values.append((field, _finite_value(getattr(container, field, None), field)))
    return SupplementalTelemetryEvent(topic, source, tuple(values), observed_at)


def _battery_diagnostics_event(
    topic: str, source: str, sample: object, observed_at: datetime
) -> SupplementalTelemetryEvent:
    """Persist every available MAVSDK battery diagnostic without inventing unknown values."""
    battery_id = getattr(sample, "id", None)
    if type(battery_id) is not int or battery_id < 0:
        raise TelemetryContractError("Telemetry battery id must be a non-negative integer.")
    values: list[tuple[str, bool | float | int | str]] = [("id", battery_id)]
    for field in (
        "temperature_degc",
        "voltage_v",
        "current_battery_a",
        "capacity_consumed_ah",
        "time_remaining_s",
    ):
        value = getattr(sample, field, None)
        if type(value) in (int, float) and isfinite(float(value)):
            values.append((field, float(value)))
    function = getattr(sample, "battery_function", None)
    function = getattr(function, "name", function)
    if isinstance(function, str) and function:
        values.append(("battery_function", function.lower()))
    return SupplementalTelemetryEvent(topic, source, tuple(values), observed_at)


def _ground_truth_event(
    topic: str, source: str, sample: object, observed_at: datetime
) -> SupplementalTelemetryEvent:
    """Preserve validated simulator truth without presenting it as onboard GPS."""
    latitude = _finite_attribute(sample, "latitude_deg")
    longitude = _finite_attribute(sample, "longitude_deg")
    if not -90.0 <= latitude <= 90.0:
        raise TelemetryContractError("Telemetry field 'latitude_deg' must be between -90.0 and 90.0.")
    if not -180.0 <= longitude <= 180.0:
        raise TelemetryContractError("Telemetry field 'longitude_deg' must be between -180.0 and 180.0.")
    return SupplementalTelemetryEvent(
        topic,
        source,
        (
            ("latitude_deg", latitude),
            ("longitude_deg", longitude),
            ("absolute_altitude_m", _finite_attribute(sample, "absolute_altitude_m")),
        ),
        observed_at,
    )


def _position_velocity_ned_event(
    topic: str, source: str, sample: object, observed_at: datetime
) -> SupplementalTelemetryEvent:
    """Preserve local estimator position and velocity in its MAVSDK NED frame."""
    position = getattr(sample, "position", None)
    velocity = getattr(sample, "velocity", None)
    fields = (
        (position, "north_m"),
        (position, "east_m"),
        (position, "down_m"),
        (velocity, "north_m_s"),
        (velocity, "east_m_s"),
        (velocity, "down_m_s"),
    )
    return SupplementalTelemetryEvent(
        topic,
        source,
        tuple((name, _finite_value(getattr(container, name, None), name)) for container, name in fields),
        observed_at,
    )


def _finite_attribute(sample: object, name: str) -> float:
    value: Any = getattr(sample, name, None)
    return _finite_value(value, name)


def _finite_value(value: Any, name: str) -> float:
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
