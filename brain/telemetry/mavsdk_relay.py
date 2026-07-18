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
    SupplementalTelemetryEvent,
    TelemetryContractError,
    TelemetryEvent,
    route_mavsdk_telemetry,
)


class MavsdkTelemetry(Protocol):
    """The read-only MAVSDK telemetry calls consumed by this relay."""

    def position(self) -> AsyncIterator[object]: ...

    def battery(self) -> AsyncIterator[object]: ...

    def in_air(self) -> AsyncIterator[bool]: ...

    def velocity_ned(self) -> AsyncIterator[object]: ...

    def attitude_euler(self) -> AsyncIterator[object]: ...

    def gps_info(self) -> AsyncIterator[object]: ...

    def flight_mode(self) -> AsyncIterator[object]: ...

    def armed(self) -> AsyncIterator[bool]: ...

    def landed_state(self) -> AsyncIterator[object]: ...

    def health(self) -> AsyncIterator[object]: ...

    def imu(self) -> AsyncIterator[object]: ...

    def ground_truth(self) -> AsyncIterator[object]: ...

    def position_velocity_ned(self) -> AsyncIterator[object]: ...


class TelemetryVehicle(Protocol):
    """Minimal vehicle boundary.  Flight-control APIs are absent by design."""

    telemetry: MavsdkTelemetry


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
        if isinstance(event, FlightStateTelemetryEvent):
            return replace(self, flight_state=event)
        return self

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
            "battery": {"remaining_percent": self.battery.remaining_percent},
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
        tasks = self._stream_tasks()
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Relay live streams until the caller requests shutdown."""
        tasks = self._stream_tasks()
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
            observed_at = self._clock()
            event = route_mavsdk_telemetry(source, sample, observed_at=observed_at)
            self._record_event(event)
            # Older adapters may expose only the required charge percentage.
            # Diagnostics are recorded when MAVSDK supplies their stable battery id;
            # they must never make the mandatory core stream unavailable.
            if source == "MAVSDK telemetry.battery" and hasattr(sample, "id"):
                self._record_event(
                    route_mavsdk_telemetry("MAVSDK telemetry.battery_diagnostics", sample, observed_at=observed_at)
                )

    def _record_event(self, event: TelemetryEvent) -> None:
        self._state = self._state.with_event(event)
        if self._on_event is not None:
            self._on_event(event)
        if self._state.complete:
            _write_atomic_json(self._destination, self._state.as_dashboard_document(self._clock()))

    def _stream_tasks(self) -> tuple[asyncio.Task[None], ...]:
        streams: list[tuple[str, AsyncIterator[object]]] = [
            ("MAVSDK telemetry.position", self._telemetry.position()),
            ("MAVSDK telemetry.battery", self._telemetry.battery()),
            ("MAVSDK telemetry.in_air", self._telemetry.in_air()),
        ]
        for name in _OPTIONAL_STREAMS:
            stream_factory = getattr(self._telemetry, name, None)
            if callable(stream_factory):
                streams.append((f"MAVSDK telemetry.{name}", stream_factory()))
        return tuple(asyncio.create_task(self._consume(source, stream)) for source, stream in streams)


_OPTIONAL_STREAMS = (
    "velocity_ned", "attitude_euler", "gps_info", "flight_mode", "armed", "landed_state", "health", "imu",
    "ground_truth",
    "position_velocity_ned",
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
