"""Read-only pymavlink telemetry relay for the dashboard snapshot."""

from __future__ import annotations

from datetime import datetime, timezone
from math import degrees, isfinite
from pathlib import Path
import time
from dataclasses import replace
from typing import Callable

from brain.mission.acceptance_inflight_px4_mode import px4_flight_mode_from_custom_mode
from brain.preflight_params.mavlink import resolve_mavlink_connection
from brain.telemetry.domain import (
    BatteryTelemetryEvent,
    FlightStateTelemetryEvent,
    PositionTelemetryEvent,
    SupplementalTelemetryEvent,
)
from brain.telemetry.link_lease import link_is_leased
from brain.telemetry.mavsdk_relay import DashboardHomePosition, DashboardTelemetryState, write_atomic_json
from brain.telemetry.autopilot_identity import (
    AutopilotIdentityCollector,
    qualifying_autopilot_heartbeat,
)
from brain.telemetry.pymavlink_passive_params import request_param_read


_TOPIC_POSITION = "telemetry/position"
_TOPIC_BATTERY = "telemetry/battery"
_TOPIC_FLIGHT = "telemetry/in_air"
_TOPIC_ATTITUDE = "telemetry/history/attitude_euler"

# PX4 EXTENDED_SYS_STATE landed_state values (MAV_LANDED_STATE_*).
_LANDED_IN_AIR = frozenset({2, 3, 4})
_CORE_MESSAGE_TYPES = frozenset(
    {
        "GLOBAL_POSITION_INT",
        "SYS_STATUS",
        "BATTERY_STATUS",
        "EXTENDED_SYS_STATE",
    }
)
# MAV_COMP_ID_AUTOPILOT1 — vehicle heartbeat, not companion/GCS component heartbeats.
_MAV_COMP_ID_AUTOPILOT1 = 1
_MAV_TYPE_GCS = 6


def mavlink_autopilot_label(autopilot_id: int) -> str:
    """Lower-case MAV_AUTOPILOT label for dashboard snapshots."""
    try:
        from pymavlink import mavutil
    except ModuleNotFoundError:
        return f"autopilot_{autopilot_id}"
    for attribute in dir(mavutil.mavlink):
        if not attribute.startswith("MAV_AUTOPILOT_"):
            continue
        value = getattr(mavutil.mavlink, attribute, None)
        if value == autopilot_id:
            return attribute.removeprefix("MAV_AUTOPILOT_").lower()
    return f"autopilot_{autopilot_id}"


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
        # Unknown/out-of-range PX4 values must not replace the last good read; the
        # dashboard snapshot has no separate battery freshness field, so refreshing
        # captured_at with a stale remaining_percent would lie to the API gateway.
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
        # Same contract rule as SYS_STATUS (see above).
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

    if message_type == "HOME_POSITION":
        return _apply_home_position_message(state, message, timestamp)

    if message_type == "PARAM_VALUE":
        return _apply_geofence_param(state, message)

    if message_type == "HEARTBEAT":
        return _apply_vehicle_heartbeat(state, message, timestamp)

    return state


def _apply_home_position_message(
    state: DashboardTelemetryState,
    message: object,
    timestamp: datetime,
) -> DashboardTelemetryState:
    # Only the autopilot whose HEARTBEAT set state.sysid may define home; a GCS or
    # companion HOME_POSITION on the same link is ignored.
    sysid = int(getattr(message, "get_srcSystem", lambda: 0)())
    component = int(getattr(message, "get_srcComponent", lambda: 0)())
    if state.sysid is None or sysid != state.sysid or component not in (0, _MAV_COMP_ID_AUTOPILOT1):
        return state
    latitude_deg = message.latitude / 1.0e7
    longitude_deg = message.longitude / 1.0e7
    absolute_altitude_m = message.altitude / 1000.0
    # Out-of-range home would make the API reject the whole snapshot, not just home.
    if not (-90.0 <= latitude_deg <= 90.0 and -180.0 <= longitude_deg <= 180.0):
        return state
    home = DashboardHomePosition(
        latitude_deg=latitude_deg,
        longitude_deg=longitude_deg,
        absolute_altitude_m=absolute_altitude_m,
        observed_at=timestamp,
    )
    return replace(state, home=home)


PX4_GEOFENCE_PARAM = "GF_MAX_HOR_DIST"
# Params can change at runtime (the failsafe acceptance profile sets them), so re-read.
GEOFENCE_REFRESH_S = 30.0


def _apply_geofence_param(state: DashboardTelemetryState, message: object) -> DashboardTelemetryState:
    """Record GF_MAX_HOR_DIST only from the heartbeat-locked autopilot."""
    name = str(getattr(message, "param_id", "")).rstrip("\x00")
    if name != PX4_GEOFENCE_PARAM or state.sysid is None:
        return state
    sysid = int(getattr(message, "get_srcSystem", lambda: 0)())
    component = int(getattr(message, "get_srcComponent", lambda: 0)())
    if sysid != state.sysid or component not in (0, _MAV_COMP_ID_AUTOPILOT1):
        return state
    value = float(getattr(message, "param_value", float("nan")))
    if not isfinite(value) or value < 0:
        return state
    return replace(state, px4_geofence_max_horizontal_m=value)


def _clear_identity_bound_telemetry(state: DashboardTelemetryState) -> DashboardTelemetryState:
    """Drop home and PX4 fence reads tied to a previous autopilot identity."""
    return replace(state, home=None, px4_geofence_max_horizontal_m=None)


def _apply_vehicle_heartbeat(
    state: DashboardTelemetryState,
    message: object,
    timestamp: datetime,
) -> DashboardTelemetryState:
    try:
        from pymavlink import mavutil
    except ModuleNotFoundError:
        return state

    vehicle_type = int(message.type)
    if vehicle_type == _MAV_TYPE_GCS:
        return state
    component = int(getattr(message, "get_srcComponent", lambda: 0)())
    if component not in (0, _MAV_COMP_ID_AUTOPILOT1):
        return state
    autopilot_id = int(message.autopilot)
    if autopilot_id != mavutil.mavlink.MAV_AUTOPILOT_PX4:
        return state
    custom_mode = int(message.custom_mode)
    armed = bool(int(message.base_mode) & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
    flight_mode = px4_flight_mode_from_custom_mode(custom_mode)
    sysid = int(getattr(message, "get_srcSystem", lambda: 0)())
    if sysid <= 0 or sysid > 255:
        return state
    return state.with_vehicle_heartbeat(
        armed=armed,
        flight_mode=flight_mode,
        sysid=sysid,
        autopilot=mavlink_autopilot_label(autopilot_id),
        observed_at=timestamp,
    )


def vehicle_identity_pending_write(before: DashboardTelemetryState, after: DashboardTelemetryState) -> bool:
    return before.vehicle_identity != after.vehicle_identity


def heartbeat_pending_write(before: DashboardTelemetryState, after: DashboardTelemetryState) -> bool:
    """Whether a HEARTBEAT should schedule a snapshot write (armed/mode/sysid changed)."""
    vehicle_fields = (
        before.armed,
        before.flight_mode,
        before.sysid,
        before.autopilot,
    )
    after_fields = (
        after.armed,
        after.flight_mode,
        after.sysid,
        after.autopilot,
    )
    return vehicle_fields != after_fields


def core_message_pending_write(
    message_type: str,
    before: DashboardTelemetryState,
    after: DashboardTelemetryState,
) -> bool:
    """Whether an inbound MAVLink message should schedule a throttled core snapshot write."""

    # HOME_POSITION is not core: writing on it would advance captured_at while
    # position/battery/in-air are stale. Home rides along with the next core write.
    if message_type not in _CORE_MESSAGE_TYPES:
        return False
    if message_type in ("SYS_STATUS", "BATTERY_STATUS"):
        return before.battery is not after.battery
    return True


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
    allow_link_lease_holder_pids: frozenset[int] | None = None,
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
    if mavlink_factory is None:
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError as error:
            raise RuntimeError(
                "pymavlink is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error

        def mavlink_factory(resolved: str) -> object:
            target = resolve_mavlink_connection(resolved)
            return mavutil.mavlink_connection(target.device, **target.kwargs)

    mavlink = mavlink_factory(connection)
    close = getattr(mavlink, "close", None)
    state = DashboardTelemetryState(relay_source="pymavlink")
    identity_collector = AutopilotIdentityCollector()
    last_write_monotonic: float | None = None
    pending_core_write = False
    last_lease_check = 0.0
    last_geofence_request: float | None = None

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
        return link_is_leased(link_lease, allow_holder_pids=allow_link_lease_holder_pids)

    def flush_snapshot() -> None:
        nonlocal last_write_monotonic, pending_core_write
        if not state.complete:
            return
        if not pending_core_write:
            return
        now = monotonic()
        if last_write_monotonic is not None and now - last_write_monotonic < write_interval_s:
            return
        captured_at = clock()
        write_atomic_json(destination, state.as_dashboard_document(captured_at))
        last_write_monotonic = now
        pending_core_write = False

    def issue_identity_requests() -> None:
        nonlocal last_geofence_request
        target = identity_collector.target
        if target is None:
            return
        target_system, target_component = target
        now = monotonic()
        if last_geofence_request is None or now - last_geofence_request >= GEOFENCE_REFRESH_S:
            last_geofence_request = now
            request_param_read(
                mavlink,
                target_system=target_system,
                target_component=target_component,
                param_name=PX4_GEOFENCE_PARAM,
            )
        for param_name in identity_collector.pop_follow_up_requests():
            request_param_read(
                mavlink,
                target_system=target_system,
                target_component=target_component,
                param_name=param_name,
            )

    def sync_identity_snapshot(before: DashboardTelemetryState) -> DashboardTelemetryState:
        updated = before.with_vehicle_identity(identity_collector.identity_block())
        if vehicle_identity_pending_write(before, updated):
            nonlocal pending_core_write
            pending_core_write = True
        return updated

    try:
        while True:
            if stop_requested():
                return
            message = mavlink.recv_match(blocking=True, timeout=recv_timeout_s)
            now = monotonic()
            if message is not None:
                before = state
                message_type = message.get_type()
                if message_type == "HEARTBEAT":
                    lock = qualifying_autopilot_heartbeat(message)
                    if lock is not None:
                        sysid, compid = lock
                        if identity_collector.observe_autopilot_heartbeat(sysid, compid, monotonic=now):
                            last_geofence_request = None
                            state = _clear_identity_bound_telemetry(sync_identity_snapshot(state))
                        issue_identity_requests()
                elif message_type == "SYSTEM_TIME":
                    time_boot_ms = int(getattr(message, "time_boot_ms", -1))
                    if time_boot_ms >= 0 and identity_collector.observe_system_time(time_boot_ms):
                        last_geofence_request = None
                        state = _clear_identity_bound_telemetry(sync_identity_snapshot(state))
                        issue_identity_requests()
                elif message_type == "PARAM_VALUE":
                    if identity_collector.apply_param_value(message):
                        issue_identity_requests()
                    state = sync_identity_snapshot(state)
                state = apply_mavlink_message(state, message, observed_at=clock())
                if core_message_pending_write(message_type, before, state):
                    pending_core_write = True
                flush_snapshot()
                continue

            if identity_collector.observe_link_quiet(monotonic=now):
                last_geofence_request = None
                state = _clear_identity_bound_telemetry(sync_identity_snapshot(state))
            else:
                identity_collector.maybe_schedule_refresh(monotonic=now)
                issue_identity_requests()
            if pending_core_write:
                flush_snapshot()
    finally:
        flush_snapshot()
        if callable(close):
            close()


__all__ = [
    "_apply_home_position_message",
    "apply_mavlink_message",
    "core_message_pending_write",
    "core_snapshot_fingerprint",
    "heartbeat_pending_write",
    "mavlink_autopilot_label",
    "run_pymavlink_relay",
    "vehicle_identity_pending_write",
]