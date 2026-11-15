"""Normalize telemetry JSON for the shared API telemetry contract."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
from math import isfinite
from pathlib import Path
from typing import Any

from brain.mission.sitl_target import TargetVerdict, classify_telemetry_document


class TelemetryFormatError(ValueError):
    """Raised when a local telemetry file is not a supported JSON object."""


@dataclass(frozen=True)
class Position:
    latitude_deg: float
    longitude_deg: float
    absolute_altitude_m: float
    relative_altitude_m: float | None


@dataclass(frozen=True)
class TelemetrySnapshot:
    position: Position | None
    battery_percent: float | None
    in_air: bool | None
    captured_at: str | None
    # Absent when the vehicle has no attitude fix. Never defaulted: a zero here
    # would aim every body-frame observation at north.
    heading_deg: float | None = None
    source: str | None = None
    armed: bool | None = None
    armed_observed_at: str | None = None
    flight_mode: str | None = None
    flight_mode_observed_at: str | None = None
    sysid: int | None = None
    sysid_observed_at: str | None = None
    autopilot: str | None = None
    autopilot_observed_at: str | None = None
    position_observed_at: str | None = None
    battery_observed_at: str | None = None
    in_air_observed_at: str | None = None
    target_class: TargetVerdict = "unknown"

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "position": None if self.position is None else {
                "latitude_deg": self.position.latitude_deg,
                "longitude_deg": self.position.longitude_deg,
                "absolute_altitude_m": self.position.absolute_altitude_m,
                "relative_altitude_m": self.position.relative_altitude_m,
            },
            "battery_percent": self.battery_percent,
            "in_air": self.in_air,
            "captured_at": self.captured_at,
            "heading_deg": self.heading_deg,
            "target_class": self.target_class,
        }
        optional = {
            "source": self.source,
            "armed": self.armed,
            "armed_observed_at": self.armed_observed_at,
            "flight_mode": self.flight_mode,
            "flight_mode_observed_at": self.flight_mode_observed_at,
            "sysid": self.sysid,
            "sysid_observed_at": self.sysid_observed_at,
            "autopilot": self.autopilot,
            "autopilot_observed_at": self.autopilot_observed_at,
            "position_observed_at": self.position_observed_at,
            "battery_observed_at": self.battery_observed_at,
            "in_air_observed_at": self.in_air_observed_at,
        }
        for key, value in optional.items():
            if value is not None:
                payload[key] = value
        return payload


def load_telemetry_snapshot(path: Path) -> TelemetrySnapshot:
    """Read a bridge payload or a mission-artifact telemetry payload from disk."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise TelemetryFormatError(f"Cannot read telemetry file: {path}") from error
    except json.JSONDecodeError as error:
        raise TelemetryFormatError("Telemetry file must contain valid JSON.") from error
    if not isinstance(document, dict):
        raise TelemetryFormatError("Telemetry payload must be a JSON object.")

    telemetry = document.get("telemetry", document)
    if not isinstance(telemetry, dict):
        raise TelemetryFormatError("Telemetry field must be a JSON object.")
    return _snapshot_from_telemetry_dict(telemetry)


def _snapshot_from_telemetry_dict(telemetry: dict[str, Any]) -> TelemetrySnapshot:
    position = _parse_position(telemetry.get("position"))
    battery = telemetry.get("battery", telemetry)
    battery_percent = _bounded_number_or_none(
        battery.get("remaining_percent", battery.get("battery_percent"))
        if isinstance(battery, dict)
        else None,
        "battery percentage",
        minimum=0.0,
        maximum=100.0,
    )
    in_air = telemetry.get("in_air")
    if in_air is not None and not isinstance(in_air, bool):
        raise TelemetryFormatError("in_air must be a boolean when present.")
    captured_at = telemetry.get("captured_at")
    if captured_at is not None and not isinstance(captured_at, str):
        raise TelemetryFormatError("captured_at must be a string when present.")
    if captured_at is not None:
        _validate_capture_time(captured_at)
    heading_deg = _bounded_number_or_none(telemetry.get("heading_deg"), "heading_deg", minimum=-360.0, maximum=360.0)
    source = _optional_string(telemetry.get("source"), "source")
    armed = telemetry.get("armed")
    if armed is not None and not isinstance(armed, bool):
        raise TelemetryFormatError("armed must be a boolean when present.")
    flight_mode = _optional_string(telemetry.get("flight_mode"), "flight_mode")
    autopilot = _optional_string(telemetry.get("autopilot"), "autopilot")
    sysid = _optional_sysid(telemetry.get("sysid"))
    return TelemetrySnapshot(
        position,
        battery_percent,
        in_air,
        captured_at,
        heading_deg,
        source=source,
        armed=armed,
        armed_observed_at=_optional_observed_at(telemetry.get("armed_observed_at")),
        flight_mode=flight_mode,
        flight_mode_observed_at=_optional_observed_at(telemetry.get("flight_mode_observed_at")),
        sysid=sysid,
        sysid_observed_at=_optional_observed_at(telemetry.get("sysid_observed_at")),
        autopilot=autopilot,
        autopilot_observed_at=_optional_observed_at(telemetry.get("autopilot_observed_at")),
        position_observed_at=_optional_observed_at(telemetry.get("position_observed_at")),
        battery_observed_at=_optional_battery_observed_at(telemetry.get("battery")),
        in_air_observed_at=_optional_observed_at(telemetry.get("in_air_observed_at")),
        target_class=classify_telemetry_document(telemetry),
    )


def _optional_string(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise TelemetryFormatError(f"{field} must be a non-empty string when present.")
    return value.strip()


def _optional_sysid(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise TelemetryFormatError("sysid must be an integer when present.")
    if not 1 <= value <= 255:
        raise TelemetryFormatError("sysid must be between 1 and 255 when present.")
    return value


def _optional_observed_at(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TelemetryFormatError("observed_at fields must be strings when present.")
    _validate_capture_time(value)
    return value


def _optional_battery_observed_at(battery: Any) -> str | None:
    if not isinstance(battery, dict):
        return None
    return _optional_observed_at(battery.get("observed_at"))


def _parse_position(value: Any) -> Position | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TelemetryFormatError("position must be an object when present.")
    return Position(
        latitude_deg=_required_bounded_number(value, "latitude_deg", minimum=-90.0, maximum=90.0),
        longitude_deg=_required_bounded_number(value, "longitude_deg", minimum=-180.0, maximum=180.0),
        absolute_altitude_m=_required_number(value, "absolute_altitude_m"),
        relative_altitude_m=_number_or_none(value.get("relative_altitude_m"), "relative_altitude_m"),
    )


def _required_number(document: dict[str, Any], field: str) -> float:
    value = _number_or_none(document.get(field), field)
    if value is None:
        raise TelemetryFormatError(f"position.{field} is required when position is present.")
    return value


def _required_bounded_number(
    document: dict[str, Any], field: str, *, minimum: float, maximum: float
) -> float:
    value = _bounded_number_or_none(document.get(field), field, minimum=minimum, maximum=maximum)
    if value is None:
        raise TelemetryFormatError(f"position.{field} is required when position is present.")
    return value


def _number_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryFormatError(f"{field} must be a number when present.")
    number = float(value)
    if not isfinite(number):
        raise TelemetryFormatError(f"{field} must be finite when present.")
    return number


def _bounded_number_or_none(value: Any, field: str, *, minimum: float, maximum: float) -> float | None:
    number = _number_or_none(value, field)
    if number is not None and not minimum <= number <= maximum:
        raise TelemetryFormatError(f"{field} must be between {minimum} and {maximum}.")
    return number


def _validate_capture_time(captured_at: str) -> None:
    try:
        parsed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
    except ValueError as error:
        raise TelemetryFormatError("captured_at must be a valid ISO 8601 timestamp.") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise TelemetryFormatError("captured_at must include a timezone offset.")