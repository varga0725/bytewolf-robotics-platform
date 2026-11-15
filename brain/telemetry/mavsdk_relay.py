"""Telemetry-only MAVSDK stream relay for the local dashboard.

The relay intentionally accepts a small structural MAVSDK boundary instead of
importing MAVSDK.  It can therefore be unit-tested on machines without either
MAVSDK or ROS 2, and it exposes no vehicle-control operation.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timezone
import json
from math import isfinite
import os
from pathlib import Path
import tempfile
from typing import AsyncIterator, Callable, Literal, Protocol

RelaySource = Literal["pymavlink", "mavsdk"]

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
    # Heading is not one of the three streams a dashboard snapshot requires: a
    # vehicle with no attitude fix still has a position worth showing. It is
    # published when known and simply absent when not, because a missing yaw
    # read as zero would point every body-frame observation at north.
    heading_deg: float | None = None
    relay_source: RelaySource | None = None
    armed: bool | None = None
    armed_observed_at: datetime | None = None
    flight_mode: str | None = None
    flight_mode_observed_at: datetime | None = None
    sysid: int | None = None
    sysid_observed_at: datetime | None = None
    autopilot: str | None = None
    autopilot_observed_at: datetime | None = None

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
        if isinstance(event, SupplementalTelemetryEvent):
            payload = dict(event.payload)
            if event.source.endswith("attitude_euler"):
                heading = payload.get("yaw_deg")
                if isinstance(heading, (int, float)) and not isinstance(heading, bool):
                    return replace(self, heading_deg=float(heading))
            if event.source.endswith(".armed"):
                armed = payload.get("value")
                if isinstance(armed, bool):
                    return replace(
                        self,
                        armed=armed,
                        armed_observed_at=event.observed_at,
                    )
            if event.source.endswith(".flight_mode"):
                mode = payload.get("value")
                if isinstance(mode, str) and mode:
                    return replace(
                        self,
                        flight_mode=mode,
                        flight_mode_observed_at=event.observed_at,
                    )
        return self

    def with_vehicle_heartbeat(
        self,
        *,
        armed: bool,
        flight_mode: str,
        sysid: int,
        autopilot: str,
        observed_at: datetime,
    ) -> DashboardTelemetryState:
        return replace(
            self,
            armed=armed,
            armed_observed_at=observed_at,
            flight_mode=flight_mode,
            flight_mode_observed_at=observed_at,
            sysid=sysid,
            sysid_observed_at=observed_at,
            autopilot=autopilot,
            autopilot_observed_at=observed_at,
        )

    def as_dashboard_document(self, captured_at: datetime) -> dict[str, object]:
        if not self.complete:
            raise ValueError("A complete telemetry snapshot requires position, battery, and flight state.")
        assert self.position is not None
        assert self.battery is not None
        assert self.flight_state is not None
        document: dict[str, object] = {
            "position": {
                "latitude_deg": self.position.latitude_deg,
                "longitude_deg": self.position.longitude_deg,
                "absolute_altitude_m": self.position.absolute_altitude_m,
                "relative_altitude_m": self.position.relative_altitude_m,
            },
            "position_observed_at": _format_timestamp(self.position.observed_at),
            "battery": {
                "remaining_percent": self.battery.remaining_percent,
                "observed_at": _format_timestamp(self.battery.observed_at),
            },
            "in_air": self.flight_state.in_air,
            "in_air_observed_at": _format_timestamp(self.flight_state.observed_at),
            "captured_at": _format_timestamp(captured_at),
        }
        if self.relay_source is not None:
            document["source"] = self.relay_source
        if self.heading_deg is not None:
            document["heading_deg"] = self.heading_deg
        if self.armed is not None:
            document["armed"] = self.armed
            if self.armed_observed_at is not None:
                document["armed_observed_at"] = _format_timestamp(self.armed_observed_at)
        if self.flight_mode is not None:
            document["flight_mode"] = self.flight_mode
            if self.flight_mode_observed_at is not None:
                document["flight_mode_observed_at"] = _format_timestamp(self.flight_mode_observed_at)
        if self.sysid is not None:
            document["sysid"] = self.sysid
            if self.sysid_observed_at is not None:
                document["sysid_observed_at"] = _format_timestamp(self.sysid_observed_at)
        if self.autopilot is not None:
            document["autopilot"] = self.autopilot
            if self.autopilot_observed_at is not None:
                document["autopilot_observed_at"] = _format_timestamp(self.autopilot_observed_at)
        return document


def dashboard_snapshot_should_write(state: DashboardTelemetryState, event: TelemetryEvent) -> bool:
    """Whether a telemetry event should refresh the on-disk dashboard snapshot.

    Supplemental streams (armed, flight_mode, attitude) update in-memory state but
    must not advance captured_at on disk without a core position/battery/flight
    observation — otherwise stale core data can look freshly captured.
    """
    if not state.complete:
        return False
    return not isinstance(event, SupplementalTelemetryEvent)


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
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._on_event = on_event
        self._state = DashboardTelemetryState(relay_source="mavsdk")

    async def run_until_streams_complete(self) -> None:
        """Relay finite streams; useful for deterministic replay and smoke tests."""
        tasks = tuple(task for _, task, _ in self._stream_tasks())
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run(self, stop_event: asyncio.Event) -> None:
        """Relay live streams until the caller requests shutdown."""
        tasks = self._stream_tasks()
        required = {task for _, task, mandatory in tasks if mandatory}
        pending = {task for _, task, _ in tasks}
        stopper = asyncio.create_task(stop_event.wait())
        try:
            while pending:
                completed, pending = await asyncio.wait(
                    (*pending, stopper), return_when=asyncio.FIRST_COMPLETED
                )
                if stopper in completed:
                    return
                for task in completed:
                    if task in required:
                        task.result()
                        raise RuntimeError("A required live MAVSDK telemetry stream ended unexpectedly.")
                    try:
                        task.result()
                    except Exception as error:
                        print(f"Optional telemetry stream stopped: {type(error).__name__}: {error}")
        finally:
            stopper.cancel()
            for _, task, _ in tasks:
                task.cancel()
            await asyncio.gather(stopper, *(task for _, task, _ in tasks), return_exceptions=True)

    async def _consume(self, source: str, stream: AsyncIterator[object]) -> None:
        async for sample in stream:
            observed_at = self._clock()
            event = route_mavsdk_telemetry(source, sample, observed_at=observed_at)
            await self._record_event(event)
            # Older adapters may expose only the required charge percentage.
            # Diagnostics are recorded when MAVSDK supplies their stable battery id;
            # they must never make the mandatory core stream unavailable.
            if source == "MAVSDK telemetry.battery" and hasattr(sample, "id"):
                try:
                    await self._record_event(
                        route_mavsdk_telemetry(
                            "MAVSDK telemetry.battery_diagnostics", sample, observed_at=observed_at
                        )
                    )
                except TelemetryContractError:
                    continue

    async def _record_event(self, event: TelemetryEvent) -> None:
        self._state = self._state.with_event(event)
        if self._on_event is not None:
            await asyncio.to_thread(self._on_event, event)
        if self._state.complete and dashboard_snapshot_should_write(self._state, event):
            document = self._state.as_dashboard_document(self._clock())
            await asyncio.to_thread(write_atomic_json, self._destination, document)

    def _stream_tasks(self) -> tuple[tuple[str, asyncio.Task[None], bool], ...]:
        streams: list[tuple[str, AsyncIterator[object], bool]] = [
            ("MAVSDK telemetry.position", self._telemetry.position(), True),
            ("MAVSDK telemetry.battery", self._telemetry.battery(), True),
            ("MAVSDK telemetry.in_air", self._telemetry.in_air(), True),
        ]
        for name in _OPTIONAL_STREAMS:
            stream_factory = getattr(self._telemetry, name, None)
            if callable(stream_factory):
                streams.append((f"MAVSDK telemetry.{name}", stream_factory(), False))
        return tuple(
            (source, asyncio.create_task(self._consume(source, stream)), mandatory)
            for source, stream, mandatory in streams
        )


_OPTIONAL_STREAMS = (
    "velocity_ned", "attitude_euler", "gps_info", "flight_mode", "armed", "landed_state", "health", "imu",
    "ground_truth",
    "position_velocity_ned",
)




def _format_timestamp(timestamp: datetime) -> str:
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("Telemetry clock must return a timezone-aware timestamp.")
    return timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def write_atomic_json(destination: Path, document: dict[str, object]) -> None:
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
