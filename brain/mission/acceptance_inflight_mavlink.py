"""MAVLink helpers for in-flight SITL acceptance (loopback only)."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from brain.mission.acceptance_inflight_px4_mode import px4_flight_mode_from_custom_mode
from brain.preflight_params.mavlink import MavlinkReadError, normalize_connection_url
from brain.navigation.relative import horizontal_distance_m
from brain.navigation.waypoints import GlobalPosition

MAV_CMD_COMPONENT_ARM_DISARM = 400
EMERGENCY_DISARM_MAGIC = 21196
MAV_RESULT_DENIED = 2
MAV_RESULT_FAILED = 4

# Mirrors GF_MAX_HOR_DIST_M in acceptance_inflight_sitl (avoid import cycle).
_GEOFENCE_HOR_BREACH_M = 100.0

DisarmOutcome = Literal["denied", "accepted", "no_ack"]


@dataclass
class DisarmAttemptResult:
    outcome: DisarmOutcome
    ack_elapsed_s: float | None = None


class GcsHeartbeatPublisher:
    """Send periodic GCS heartbeats so PX4 tracks datalink while enabled."""

    def __init__(self, connection_url: str, *, interval_s: float = 1.0) -> None:
        self._url = normalize_connection_url(connection_url)
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._mavlink = None

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError as error:
            raise MavlinkReadError(
                "pymavlink is not installed. Run: .venv/bin/pip install -r requirements.txt"
            ) from error
        self._mavlink = mavutil.mavlink_connection(self._url)
        if self._mavlink.wait_heartbeat(timeout=10.0) is None:
            raise MavlinkReadError(f"No MAVLink heartbeat on {self._url!r}")
        self._thread = threading.Thread(target=self._run, name="gcs-heartbeat", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._mavlink is not None:
            self._close_mavlink(self._mavlink)
            self._mavlink = None

    @staticmethod
    def _close_mavlink(mavlink: object) -> None:
        close = getattr(mavlink, "close", None)
        if callable(close):
            close()

    def _run(self) -> None:
        from pymavlink import mavutil

        assert self._mavlink is not None
        while not self._stop.is_set():
            self._mavlink.mav.heartbeat_send(
                mavutil.mavlink.MAV_TYPE_GCS,
                mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                0,
                0,
                0,
            )
            self._stop.wait(self._interval_s)


def _mavlink_connection(connection_url: str, *, timeout_s: float = 10.0):
    try:
        from pymavlink import mavutil
    except ModuleNotFoundError as error:
        raise MavlinkReadError(
            "pymavlink is not installed. Run: .venv/bin/pip install -r requirements.txt"
        ) from error
    url = normalize_connection_url(connection_url)
    mavlink = mavutil.mavlink_connection(url)
    if mavlink.wait_heartbeat(timeout=timeout_s) is None:
        raise MavlinkReadError(f"No MAVLink heartbeat on {url!r} within {timeout_s:.1f} s")
    return mavlink


def send_emergency_kill(connection_url: str) -> None:
    """QGroundControl-style emergency stop (SITL validation only)."""
    mavlink = _mavlink_connection(connection_url)
    try:
        mavlink.mav.command_long_send(
            mavlink.target_system,
            mavlink.target_component,
            MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0.0,
            float(EMERGENCY_DISARM_MAGIC),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
    finally:
        GcsHeartbeatPublisher._close_mavlink(mavlink)


def send_normal_disarm(connection_url: str) -> DisarmAttemptResult:
    """Attempt a normal disarm on the same MAVLink link used for kill timing."""
    start = time.monotonic()
    mavlink = _mavlink_connection(connection_url)
    try:
        mavlink.mav.command_long_send(
            mavlink.target_system,
            mavlink.target_component,
            MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            message = mavlink.recv_match(type="COMMAND_ACK", blocking=True, timeout=1.0)
            if message is None:
                continue
            if int(message.command) != MAV_CMD_COMPONENT_ARM_DISARM:
                continue
            elapsed = time.monotonic() - start
            result = int(message.result)
            if result == 0:
                return DisarmAttemptResult("accepted", ack_elapsed_s=elapsed)
            if result in {MAV_RESULT_DENIED, MAV_RESULT_FAILED}:
                return DisarmAttemptResult("denied", ack_elapsed_s=elapsed)
            return DisarmAttemptResult("no_ack")
        return DisarmAttemptResult("no_ack")
    finally:
        GcsHeartbeatPublisher._close_mavlink(mavlink)


@dataclass
class KillInAirProbeResult:
    disarm: DisarmAttemptResult
    kill_to_disarm_s: float | None


def wait_until_disarmed_via_mavlink(connection_url: str, *, timeout_s: float = 5.0) -> float | None:
    """Return seconds until disarm is seen on HEARTBEAT, or None on timeout."""
    from pymavlink import mavutil

    start = time.monotonic()
    mavlink = _mavlink_connection(connection_url)
    try:
        deadline = start + timeout_s
        while time.monotonic() < deadline:
            message = mavlink.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if message is None:
                continue
            armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not armed:
                return time.monotonic() - start
        return None
    finally:
        GcsHeartbeatPublisher._close_mavlink(mavlink)


def probe_kill_in_air(connection_url: str, *, timeout_s: float = 3.0) -> KillInAirProbeResult:
    """Normal disarm attempt then emergency kill on one MAVLink session; timer starts before kill send."""
    from pymavlink import mavutil

    start = time.monotonic()
    mavlink = _mavlink_connection(connection_url)
    try:
        mavlink.mav.command_long_send(
            mavlink.target_system,
            mavlink.target_component,
            MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        disarm_outcome: DisarmOutcome = "no_ack"
        disarm_elapsed: float | None = None
        disarm_deadline = time.monotonic() + 5.0
        while time.monotonic() < disarm_deadline:
            message = mavlink.recv_match(type="COMMAND_ACK", blocking=True, timeout=1.0)
            if message is None:
                continue
            if int(message.command) != MAV_CMD_COMPONENT_ARM_DISARM:
                continue
            disarm_elapsed = time.monotonic() - start
            result = int(message.result)
            disarm_outcome = "accepted" if result == 0 else "denied"
            break
        disarm = DisarmAttemptResult(disarm_outcome, ack_elapsed_s=disarm_elapsed)

        kill_send_at = time.monotonic()
        mavlink.mav.command_long_send(
            mavlink.target_system,
            mavlink.target_component,
            MAV_CMD_COMPONENT_ARM_DISARM,
            0,
            0.0,
            float(EMERGENCY_DISARM_MAGIC),
            0.0,
            0.0,
            0.0,
            0.0,
            0.0,
        )
        deadline = kill_send_at + timeout_s
        while time.monotonic() < deadline:
            message = mavlink.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if message is None:
                continue
            armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not armed:
                return KillInAirProbeResult(disarm, time.monotonic() - kill_send_at)
        return KillInAirProbeResult(disarm, None)
    finally:
        GcsHeartbeatPublisher._close_mavlink(mavlink)


@dataclass
class MavlinkSample:
    monotonic_s: float
    armed: bool | None = None
    in_air: bool | None = None
    flight_mode: str | None = None
    battery_percent: float | None = None
    relative_altitude_m: float | None = None
    horizontal_distance_from_home_m: float | None = None
    geofence_breach: bool | None = None
    statustext: str | None = None


@dataclass
class MavlinkTelemetryPoller:
    """Background MAVLink telemetry used when MAVSDK is disconnected (datalink loss)."""

    connection_url: str
    origin_s: float = field(default_factory=time.monotonic)
    home: GlobalPosition | None = None
    samples: list[MavlinkSample] = field(default_factory=list)
    geofence_breach_at_s: float | None = None
    _stop: threading.Event = field(default_factory=threading.Event, repr=False)
    _thread: threading.Thread | None = field(default=None, repr=False)
    _mavlink: object | None = field(default=None, repr=False)

    def start(self) -> None:
        if self._thread is not None:
            return
        self._mavlink = _mavlink_connection(self.connection_url)
        self._thread = threading.Thread(target=self._run, name="mavlink-poller", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._mavlink is not None:
            GcsHeartbeatPublisher._close_mavlink(self._mavlink)
            self._mavlink = None

    def _elapsed(self) -> float:
        return time.monotonic() - self.origin_s

    def _run(self) -> None:
        from pymavlink import mavutil

        assert self._mavlink is not None
        last_position: GlobalPosition | None = None
        while not self._stop.is_set():
            message = self._mavlink.recv_match(blocking=True, timeout=0.5)
            if message is None:
                continue
            sample = MavlinkSample(monotonic_s=self._elapsed())
            message_type = message.get_type()
            if message_type == "HEARTBEAT":
                custom = int(message.custom_mode)
                sample.armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                sample.flight_mode = px4_flight_mode_from_custom_mode(custom)
                if last_position is not None and self.home is not None:
                    sample.horizontal_distance_from_home_m = horizontal_distance_m(self.home, last_position)
                if sample.relative_altitude_m is None and last_position is not None and self.home is not None:
                    sample.relative_altitude_m = last_position.absolute_altitude_m - self.home.absolute_altitude_m
            elif message_type == "GLOBAL_POSITION_INT":
                latitude_deg = int(message.lat) / 1.0e7
                longitude_deg = int(message.lon) / 1.0e7
                absolute_altitude_m = int(message.alt) / 1000.0
                relative_altitude_m = int(message.relative_alt) / 1000.0
                last_position = GlobalPosition(latitude_deg, longitude_deg, absolute_altitude_m)
                sample.relative_altitude_m = relative_altitude_m
                if self.home is not None:
                    sample.horizontal_distance_from_home_m = horizontal_distance_m(self.home, last_position)
            elif message_type == "EXTENDED_SYS_STATE":
                landed = int(message.landed_state)
                sample.in_air = landed in {2, 3, 4}
            elif message_type == "SYS_STATUS":
                remaining = int(message.battery_remaining)
                if 0 <= remaining <= 100:
                    sample.battery_percent = float(remaining)
            elif message_type == "BATTERY_STATUS":
                remaining = int(message.battery_remaining)
                if 0 <= remaining <= 100:
                    sample.battery_percent = float(remaining)
            elif message_type == "STATUSTEXT":
                sample.statustext = str(message.text).strip("\x00")
            else:
                continue
            self._maybe_mark_geofence_breach(sample)
            self.samples.append(sample)

    def _maybe_mark_geofence_breach(self, sample: MavlinkSample) -> None:
        if self.geofence_breach_at_s is not None:
            return
        text = (sample.statustext or "").lower()
        if "geofence" in text:
            sample.geofence_breach = True
            self.geofence_breach_at_s = sample.monotonic_s
            return
        hor = sample.horizontal_distance_from_home_m
        mode = (sample.flight_mode or "").lower()
        fence_mode = mode in {"return_to_launch", "loiter", "land", "position", "hold"}
        if hor is not None and hor >= _GEOFENCE_HOR_BREACH_M - 1.0 and fence_mode:
            sample.geofence_breach = True
            self.geofence_breach_at_s = sample.monotonic_s