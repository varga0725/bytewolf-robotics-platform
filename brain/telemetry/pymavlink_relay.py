"""Read-only pymavlink telemetry relay for the dashboard snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from math import degrees, isfinite
from pathlib import Path
import time
from typing import Callable

from brain.preflight_params.mavlink import normalize_connection_url
from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    SupplementalTelemetryEvent,
)
from brain.telemetry.link_lease import link_is_leased
from brain.telemetry.mavsdk_relay import DashboardTelemetryState, write_atomic_json


_TOPIC_POSITION = "telemetry/position"
_TOPIC_BATTERY = "telemetry/battery"
_TOPIC_FLIGHT = "telemetry/in_air"
_TOPIC_ATTITUDE = "telemetry/history/attitude_euler"

# PX4 EXTENDED_SYS_STATE landed_state values (MAV_LANDED_STATE_*).
_LANDED_IN_AIR = frozenset({2, 3, 4})


def apply_mavlink_message(
    state: DashboardTelemetryState,
    message: object,
    *,
    observed_at: datetime | None = None,
) -> DashboardTelemetryState:
    """Fold one inbound MAVLink message into dashboard relay state."""

    timestamp = observed_at or datetime.now(timezone.utc)
    message_type = message.get_type()
    if message_type == "GLOBAL_POSITION_INT":
        latitude_deg = message.lat / 1.0e7
        longitude_deg = message.lon / 1.0e7
        absolute_altitude_m = message.alt / 1000.0
        relative_altitude_m = message.relative_alt / 1000.0
        if not all(
            isfinite(value)
            for value in (latitude_deg, longitude_deg, absolute_altitude_m, relative_altitude_m)
        ):
            return state
        position = PositionTelemetryEvent(
            topic=_TOPIC_POSITION,
            latitude_deg=latitude_deg,
            longitude_deg=longitude_deg,
            absolute_altitude_m=absolute_altitude_m,
            relative_altitude_m=relative_altitude_m,
            observed_at=timestamp,
        )
        return state.with_event(position)

    if message_type == "SYS_STATUS":
        remaining = int(message.battery_remaining)
        if remaining < 0 or remaining > 100:
            return state
        return state.with_event(
            BatteryTelemetryEvent(
                topic=_TOPIC_BATTERY,
                remaining_percent=float(remaining),
                observed_at=timestamp,
            )
        )

    if message_type == "BATTERY_STATUS":
        remaining = int(message.battery_remaining)
        if remaining < 0 or remaining > 100:
            return state
        return state.with_event(
            BatteryTelemetryEvent(
                topic=_TOPIC_BATTERY,
                remaining_percent=float(remaining),
                observed_at=timestamp,
            )
        )

    if message_type == "EXTENDED_SYS_STATE":
        landed_state = int(message.landed_state)
        return state.with_event(
            FlightStateTelemetryEvent(
                topic=_TOPIC_FLIGHT,
                in_air=landed_state in _LANDED_IN_AIR,
                observed_at=timestamp,
            )
        )

    if message_type == "ATTITUDE":
        yaw_rad = float(message.yaw)
        if not isfinite(yaw_rad):
            return state
        heading_deg = _normalize_heading_deg(degrees(yaw_rad))
        event = SupplementalTelemetryEvent(
            topic=_TOPIC_ATTITUDE,
            source="pymavlink.attitude_euler",
            payload=(("yaw_deg", heading_deg),),
            observed_at=timestamp,
        )
        return state.with_event(event)

    return state


def core_snapshot_fingerprint(state: DashboardTelemetryState) -> tuple[float, ...] | None:
    """Hash the three mandatory dashboard fields; heading changes are excluded."""

    if not state.complete:
        return None
    assert state.position is not None
    assert state.battery is not None
    assert state.flight_state is not None
    return (
        state.position.latitude_deg,
        state.position.longitude_deg,
        state.position.absolute_altitude_m,
        state.position.relative_altitude_m,
        state.battery.remaining_percent,
        float(state.flight_state.in_air),
    )


def _normalize_heading_deg(yaw_deg: float) -> float:
    wrapped = yaw_deg % 360.0
    if wrapped < 0:
        wrapped += 360.0
    return wrapped


def run_pymavlink_relay(
    connection: str,
    destination: Path,
    *,
    recv_timeout_s: float = 1.0,
    write_interval_s: float = 0.2,
    lease_poll_s: float = 0.5,
    link_lease: Path | None = None,
    clock: Callable[[], datetime] | None = None,
    mavlink_factory: Callable[[str], object] | None = None,
    should_stop: Callable[[], bool] | None = None,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Receive MAVLink on a passive connection and atomically update the snapshot."""

    if write_interval_s <= 0:
        raise ValueError("write_interval_s must be positive.")

    clock = clock or (lambda: datetime.now(timezone.utc))
    url = normalize_connection_url(connection)
    if mavlink_factory is None:
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "pymavlink is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error

        def mavlink_factory(resolved: str) -> object:
            return mavutil.mavlink_connection(resolved)

    mavlink = mavlink_factory(url)
    close = getattr(mavlink, "close", None)
    state = DashboardTelemetryState()
    last_core_fingerprint: tuple[float, ...] | None = None
    last_write_monotonic: float | None = None
    pending_core_write = False
    last_lease_check = 0.0

    def stop_requested() -> bool:
        nonlocal last_lease_check
        if should_stop is not None and should_stop():
            return True
        if link_lease is None:
            return False
        now = monotonic()
        if now - last_lease_check < lease_poll_s:
            return False
        last_lease_check = now
        return link_is_leased(link_lease)

    def flush_snapshot(*, force: bool) -> None:
        nonlocal last_write_monotonic, pending_core_write, last_core_fingerprint
        if not state.complete:
            return
        now = monotonic()
        fingerprint = core_snapshot_fingerprint(state)
        if fingerprint is None:
            return
        if not force:
            if not pending_core_write:
                return
            if last_write_monotonic is not None and now - last_write_monotonic < write_interval_s:
                return
        captured_at = clock()
        write_atomic_json(destination, state.as_dashboard_document(captured_at))
        last_write_monotonic = now
        pending_core_write = False
        last_core_fingerprint = fingerprint

    try:
        while True:
            if stop_requested():
                return
            message = mavlink.recv_match(blocking=True, timeout=recv_timeout_s)
            now = monotonic()
            if message is not None:
                previous = state
                state = apply_mavlink_message(state, message, observed_at=clock())
                if state != previous:
                    fingerprint = core_snapshot_fingerprint(state)
                    if fingerprint is not None and fingerprint != last_core_fingerprint:
                        pending_core_write = True
                    flush_snapshot(force=False)
                continue

            if pending_core_write:
                flush_snapshot(force=False)
    finally:
        flush_snapshot(force=True)
        if callable(close):
            close()


__all__ = [
    "apply_mavlink_message",
    "core_snapshot_fingerprint",
    "run_pymavlink_relay",
]