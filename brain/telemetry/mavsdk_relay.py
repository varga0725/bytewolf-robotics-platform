"""Telemetry-only MAVSDK stream relay for the local dashboard.

The relay intentionally accepts a small structural MAVSDK boundary instead of
importing MAVSDK.  It can therefore be unit-tested on machines without either
MAVSDK or ROS 2, and it exposes no vehicle-control operation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
from typing import AsyncIterator, Callable, Protocol

from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    TelemetryContractError,
    TelemetryEvent,
    route_mavsdk_telemetry,
)


class MavsdkTelemetry(Protocol):
    """The read-only MAVSDK telemetry calls consumed by this relay."""

    def position(self) -> AsyncIterator[object]: ...

    def battery(self) -> AsyncIterator[object]: ...

    def in_air(self) -> AsyncIterator[bool]: ...


class TelemetryVehicle(Protocol):
    """Minimal vehicle boundary.  Flight-control APIs are absent by design."""

    telemetry: MavsdkTelemetry


@dataclass(frozen=True)
class _NormalizedBatterySample:
    """Fractional battery value accepted by the telemetry-domain boundary."""

    remaining_percent: float


@dataclass(frozen=True)
class DashboardTelemetryState:
    """Immutable dashboard state collected from declared telemetry sources."""

    position: PositionTelemetryEvent | None = None
    battery: BatteryTelemetryEvent | None = None
    flight_state: FlightStateTelemetryEvent | None = None

    @property
    def complete(self) -> bool:
        return self.position is not None and self.battery is not None and self.flight_state is not None

    def with_event(self, event: TelemetryEvent) -> DashboardTelemetryState:
        if isinstance(event, PositionTelemetryEvent):
            return replace(self, position=event)
        if isinstance(event, BatteryTelemetryEvent):
            return replace(self, battery=event)
        return replace(self, flight_state=event)

    def as_dashboard_document(self, captured_at: datetime) -> dict[str, object]:
        if not self.complete:
            raise ValueError("A complete telemetry snapshot requires position, battery, and flight state.")
        assert self.position is not None
        assert self.battery is not None
        assert self.flight_state is not None
        return {
            "position": {
                "latitude_deg": self.position.latitude_deg,
                "longitude_deg": self.position.longitude_deg,
                "absolute_altitude_m": self.position.absolute_altitude_m,
                "relative_altitude_m": self.position.relative_altitude_m,
            },
            "battery": {"remaining_percent": self.battery.remaining_percent * 100.0},
            "in_air": self.flight_state.in_air,
            "captured_at": _format_timestamp(captured_at),
        }


class MavsdkTelemetryRelay:
    """Consume only declared MAVSDK telemetry and atomically update dashboard JSON."""

    def __init__(
        self,
        vehicle: TelemetryVehicle,
        destination: Path,
        *,
        clock: Callable[[], datetime] | None = None,
        on_event: Callable[[TelemetryEvent], None] | None = None,
    ) -> None:
        self._telemetry = vehicle.telemetry
        self._destination = destination
        self._clock = clock or (lambda: datetime.now(UTC))
        self._on_event = on_event
        self._state = DashboardTelemetryState()

    async def run_until_streams_complete(self) -> None:
        """Relay finite streams; useful for deterministic replay and smoke tests."""
        tasks = (
            asyncio.create_task(self._consume("MAVSDK telemetry.position", self._telemetry.position())),
            asyncio.create_task(self._consume("MAVSDK telemetry.battery", self._telemetry.battery())),
            asyncio.create_task(self._consume("MAVSDK telemetry.in_air", self._telemetry.in_air())),
        )
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Relay live streams until the caller requests shutdown."""
        tasks = (
            asyncio.create_task(self._consume("MAVSDK telemetry.position", self._telemetry.position())),
            asyncio.create_task(self._consume("MAVSDK telemetry.battery", self._telemetry.battery())),
            asyncio.create_task(self._consume("MAVSDK telemetry.in_air", self._telemetry.in_air())),
        )
        stopper = asyncio.create_task(stop_event.wait())
        try:
            completed, _ = await asyncio.wait((*tasks, stopper), return_when=asyncio.FIRST_COMPLETED)
            if stopper not in completed:
                for task in completed:
                    task.result()
                raise RuntimeError("A live MAVSDK telemetry stream ended unexpectedly.")
        finally:
            stopper.cancel()
            for task in tasks:
                task.cancel()
            await asyncio.gather(stopper, *tasks, return_exceptions=True)

    async def _consume(self, source: str, stream: AsyncIterator[object]) -> None:
        async for sample in stream:
            event = route_mavsdk_telemetry(
                source,
                _normalize_battery_sample(source, sample),
                observed_at=self._clock(),
            )
            self._state = self._state.with_event(event)
            if self._on_event is not None:
                self._on_event(event)
            if self._state.complete:
                _write_atomic_json(self._destination, self._state.as_dashboard_document(self._clock()))


def _normalize_battery_sample(source: str, sample: object) -> object:
    """Adapt the MAVSDK 0–100 battery variant without relaxing domain checks.

    MAVSDK integrations may report either a 0–1 fraction or a 2–100 percentage.
    Values between the representations are rejected because their unit is unclear.
    """
    if source != "MAVSDK telemetry.battery":
        return sample
    value = getattr(sample, "remaining_percent", None)
    if type(value) not in (int, float) or not isfinite(float(value)):
        raise TelemetryContractError("Telemetry field 'remaining_percent' must be a finite number.")
    remaining_percent = float(value)
    if 0.0 <= remaining_percent <= 1.0:
        return sample
    if 2.0 <= remaining_percent <= 100.0:
        return _NormalizedBatterySample(remaining_percent / 100.0)
    if 1.0 < remaining_percent < 2.0:
        raise TelemetryContractError(
            "Telemetry field 'remaining_percent' is ambiguous between fractional and percentage units."
        )
    raise TelemetryContractError(
        "Telemetry field 'remaining_percent' must be a 0.0 to 1.0 fraction or a 2.0 to 100.0 percentage."
    )


def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Telemetry clock must return a timezone-aware timestamp.")
    return timestamp.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _write_atomic_json(destination: Path, document: dict[str, object]) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, separators=(",", ":"), allow_nan=False)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=".telemetry-", suffix=".tmp", dir=destination.parent, text=True
    )
    try:
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as temporary_file:
            temporary_file.write(payload)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_name, destination)
    except BaseException:
        Path(temporary_name).unlink(missing_ok=True)
        raise
