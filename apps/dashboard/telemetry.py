"""Normalize telemetry JSON for the read-only local dashboard."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any


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

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


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
    position = _parse_position(telemetry.get("position"))
    battery = telemetry.get("battery", telemetry)
    battery_percent = _number_or_none(
        battery.get("remaining_percent", battery.get("battery_percent"))
        if isinstance(battery, dict)
        else None,
        "battery percentage",
    )
    in_air = telemetry.get("in_air")
    if in_air is not None and not isinstance(in_air, bool):
        raise TelemetryFormatError("in_air must be a boolean when present.")
    captured_at = telemetry.get("captured_at")
    if captured_at is not None and not isinstance(captured_at, str):
        raise TelemetryFormatError("captured_at must be a string when present.")
    return TelemetrySnapshot(position, battery_percent, in_air, captured_at)


def _parse_position(value: Any) -> Position | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TelemetryFormatError("position must be an object when present.")
    return Position(
        latitude_deg=_required_number(value, "latitude_deg"),
        longitude_deg=_required_number(value, "longitude_deg"),
        absolute_altitude_m=_required_number(value, "absolute_altitude_m"),
        relative_altitude_m=_number_or_none(value.get("relative_altitude_m"), "relative_altitude_m"),
    )


def _required_number(document: dict[str, Any], field: str) -> float:
    value = _number_or_none(document.get(field), field)
    if value is None:
        raise TelemetryFormatError(f"position.{field} is required when position is present.")
    return value


def _number_or_none(value: Any, field: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TelemetryFormatError(f"{field} must be a number when present.")
    return float(value)
