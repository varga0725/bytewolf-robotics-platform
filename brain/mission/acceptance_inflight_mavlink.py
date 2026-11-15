"""MAVLink helpers for in-flight SITL acceptance (loopback only)."""

from __future__ import annotations

import threading
import time
from typing import Callable

from brain.preflight_params.mavlink import MavlinkReadError, normalize_connection_url

MAV_CMD_COMPONENT_ARM_DISARM = 400
EMERGENCY_DISARM_MAGIC = 21196


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
            close = getattr(self._mavlink, "close", None)
            if callable(close):
                close()
            self._mavlink = None

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
        close = getattr(mavlink, "close", None)
        if callable(close):
            close()


def send_normal_disarm(connection_url: str) -> bool:
    """Attempt a normal disarm; return True when PX4 accepts COMMAND_ACK result."""
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
            # MAV_RESULT_ACCEPTED = 0; DENIED = 2; FAILED = 4
            return int(message.result) == 0
        return False
    finally:
        close = getattr(mavlink, "close", None)
        if callable(close):
            close()


def wait_until_disarmed_via_mavlink(
    connection_url: str,
    *,
    timeout_s: float,
    poll: Callable[[], float] = time.monotonic,
) -> float | None:
    """Return seconds until HEARTBEAT reports disarmed, or None on timeout."""
    from pymavlink import mavutil

    mavlink = _mavlink_connection(connection_url, timeout_s=min(10.0, timeout_s))
    try:
        start = poll()
        deadline = start + timeout_s
        while poll() < deadline:
            message = mavlink.recv_match(type="HEARTBEAT", blocking=True, timeout=0.5)
            if message is None:
                continue
            armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
            if not armed:
                return poll() - start
        return None
    finally:
        close = getattr(mavlink, "close", None)
        if callable(close):
            close()