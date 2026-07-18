"""Append-only, read-only replay storage for already-validated telemetry events."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
from math import isfinite
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    SupplementalTelemetryEvent,
    TelemetryContractError,
    TelemetryEvent,
    route_mavsdk_telemetry,
)


TELEMETRY_HISTORY_VERSION = "v0.1"


@dataclass(frozen=True)
class TelemetryHistoryStore:
    """Persist validated events as a durable JSONL sequence without control access."""

    destination: Path

    def append(self, event: TelemetryEvent) -> None:
        """Append one immutable, timestamped event after serializing it canonically."""
        document = _event_document(event)
        payload = json.dumps(document, allow_nan=False, separators=(",", ":"), sort_keys=True)
        self.destination.parent.mkdir(parents=True, exist_ok=True)
        with self.destination.open("a", encoding="utf-8") as output:
            output.write(f"{payload}\n")
            output.flush()


def load_telemetry_history(path: Path) -> tuple[TelemetryEvent, ...]:
    """Load a durable event sequence for offline replay; it cannot contact a vehicle."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError(f"Cannot read telemetry history '{path}': {error.strerror}.") from error
    events: list[TelemetryEvent] = []
    previous_at: datetime | None = None
    for line_number, line in enumerate(lines, start=1):
        if not line:
            raise ValueError(f"Telemetry history line {line_number} is empty.")
        try:
            document = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"Telemetry history line {line_number} is not valid JSON.") from error
        event = _load_event(document, line_number)
        if previous_at is not None and event.observed_at < previous_at:
            raise ValueError("Telemetry history events are out of chronological order.")
        events.append(event)
        previous_at = event.observed_at
    return tuple(events)


def _event_document(event: TelemetryEvent) -> dict[str, object]:
    common: dict[str, object] = {
        "observed_at": _format_timestamp(event.observed_at),
        "topic": event.topic,
        "version": TELEMETRY_HISTORY_VERSION,
    }
    if isinstance(event, PositionTelemetryEvent):
        return {
            **common,
            "absolute_altitude_m": event.absolute_altitude_m,
            "event_type": "position",
            "latitude_deg": event.latitude_deg,
            "longitude_deg": event.longitude_deg,
            "relative_altitude_m": event.relative_altitude_m,
        }
    if isinstance(event, BatteryTelemetryEvent):
        return {**common, "event_type": "battery", "remaining_percent": event.remaining_percent}
    if isinstance(event, SupplementalTelemetryEvent):
        return {
            **common,
            "event_type": "supplemental",
            "payload": dict(event.payload),
            "source": event.source,
        }
    return {**common, "event_type": "flight_state", "in_air": event.in_air}


def _load_event(document: object, line_number: int) -> TelemetryEvent:
    if not isinstance(document, dict):
        raise ValueError(f"Telemetry history line {line_number} must be an object.")
    if _required_string(document, "version", line_number) != TELEMETRY_HISTORY_VERSION:
        raise ValueError(f"Telemetry history line {line_number} has an unsupported version.")
    topic = _required_string(document, "topic", line_number)
    observed_at = _timestamp(_required_string(document, "observed_at", line_number), line_number)
    event_type = _required_string(document, "event_type", line_number)
    if event_type == "position":
        latitude = _bounded_number(document, "latitude_deg", line_number, -90.0, 90.0)
        longitude = _bounded_number(document, "longitude_deg", line_number, -180.0, 180.0)
        return PositionTelemetryEvent(
            topic,
            latitude,
            longitude,
            _number(document, "absolute_altitude_m", line_number),
            _number(document, "relative_altitude_m", line_number),
            observed_at,
        )
    if event_type == "battery":
        return BatteryTelemetryEvent(
            topic, _bounded_number(document, "remaining_percent", line_number, 0.0, 100.0), observed_at
        )
    if event_type == "flight_state":
        value = document.get("in_air")
        if type(value) is not bool:
            raise ValueError(f"Telemetry history line {line_number} in_air must be a boolean.")
        return FlightStateTelemetryEvent(topic, value, observed_at)
    if event_type == "supplemental":
        source = _required_string(document, "source", line_number)
        payload = document.get("payload")
        if not isinstance(payload, dict) or not payload:
            raise ValueError(f"Telemetry history line {line_number} supplemental payload must be an object.")
        for key, value in sorted(payload.items()):
            if not isinstance(key, str) or type(value) not in (bool, int, float, str):
                raise ValueError(f"Telemetry history line {line_number} supplemental payload is invalid.")
            if type(value) in (int, float) and not isfinite(float(value)):
                raise ValueError(f"Telemetry history line {line_number} supplemental payload must be finite.")
        try:
            event = route_mavsdk_telemetry(
                source,
                _supplemental_sample(source, payload),
                observed_at=observed_at,
            )
        except TelemetryContractError as error:
            raise ValueError(f"Telemetry history line {line_number} {error}.") from error
        if not isinstance(event, SupplementalTelemetryEvent):
            raise ValueError(f"Telemetry history line {line_number} supplemental source did not produce supplemental telemetry.")
        if event.topic != topic or dict(event.payload) != payload:
            raise ValueError(
                f"Telemetry history line {line_number} supplemental payload does not match the declared source schema."
            )
        return event
    raise ValueError(f"Telemetry history line {line_number} has an unknown event_type.")


def _supplemental_sample(source: str, payload: dict[str, bool | float | int | str]) -> object:
    if source == "MAVSDK telemetry.position_velocity_ned":
        return SimpleNamespace(
            position=SimpleNamespace(
                north_m=payload.get("north_m"),
                east_m=payload.get("east_m"),
                down_m=payload.get("down_m"),
            ),
            velocity=SimpleNamespace(
                north_m_s=payload.get("north_m_s"),
                east_m_s=payload.get("east_m_s"),
                down_m_s=payload.get("down_m_s"),
            ),
        )
    if source == "MAVSDK telemetry.imu":
        return SimpleNamespace(
            acceleration_frd=SimpleNamespace(
                forward_m_s2=payload.get("forward_m_s2"),
                right_m_s2=payload.get("right_m_s2"),
                down_m_s2=payload.get("down_m_s2"),
            ),
            angular_velocity_frd=SimpleNamespace(
                forward_rad_s=payload.get("forward_rad_s"),
                right_rad_s=payload.get("right_rad_s"),
                down_rad_s=payload.get("down_rad_s"),
            ),
            magnetic_field_frd=SimpleNamespace(
                forward_gauss=payload.get("forward_gauss"),
                right_gauss=payload.get("right_gauss"),
                down_gauss=payload.get("down_gauss"),
            ),
            temperature_degc=payload.get("temperature_degc"),
        )
    return SimpleNamespace(**payload)


def _number(document: dict[str, Any], field: str, line_number: int) -> float:
    value = document.get(field)
    if type(value) not in (int, float):
        raise ValueError(f"Telemetry history line {line_number} {field} must be a number.")
    converted = float(value)
    if not isfinite(converted):
        raise ValueError(f"Telemetry history line {line_number} {field} must be finite.")
    return converted


def _bounded_number(
    document: dict[str, Any], field: str, line_number: int, minimum: float, maximum: float
) -> float:
    value = _number(document, field, line_number)
    if not minimum <= value <= maximum:
        raise ValueError(
            f"Telemetry history line {line_number} {field} must be between {minimum} and {maximum}."
        )
    return value


def _required_string(document: dict[str, Any], field: str, line_number: int) -> str:
    value = document.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Telemetry history line {line_number} {field} must be a non-empty string.")
    return value


def _timestamp(value: str, line_number: int) -> datetime:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"Telemetry history line {line_number} observed_at must be RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError(f"Telemetry history line {line_number} observed_at must include an offset.")
    return timestamp.astimezone(UTC)


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Telemetry event timestamp must include an offset.")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")
