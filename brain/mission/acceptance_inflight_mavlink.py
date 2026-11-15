"""MAVLink helpers for in-flight SITL acceptance (loopback only)."""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from typing import Literal

from brain.preflight_params.mavlink import MavlinkReadError, normalize_connection_url

MAV_CMD_COMPONENT_ARM_DISARM = 400
EMERGENCY_DISARM_MAGIC = 21196
MAV_RESULT_DENIED = 2
MAV_RESULT_FAILED = 4

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
            return DisarmAttemptResult("denied", ack_elapsed_s=elapsed)
        return DisarmAttemptResult("no_ack")
    finally:
        GcsHeartbeatPublisher._close_mavlink(mavlink)


def wait_until_disarmed_via_mavlink(connection_url: str, *, timeout_s: float) -> float | None:
    """Return seconds until HEARTBEAT reports disarmed, using one connection."""
    from pymavlink import mavutil

    mavlink = _mavlink_connection(connection_url, timeout_s=min(10.0, timeout_s))
    try:
        start = time.monotonic()
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


@dataclass
class MavlinkSample:
    monotonic_s: float
    armed: bool | None = None
    in_air: bool | None = None
    flight_mode: str | None = None
    battery_percent: float | None = None
    geofence_breach: bool | None = None
    statustext: str | None = None


@dataclass
class MavlinkTelemetryPoller:
    """Background MAVLink telemetry used when MAVSDK is disconnected (datalink loss)."""

    connection_url: str
    origin_s: float = field(default_factory=time.monotonic)
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
        mode_map = {
            0: "manual",
            4: "guided",
            5: "auto",
            6: "rtl",
            20: "mission",
        }
        while not self._stop.is_set():
            message = self._mavlink.recv_match(blocking=True, timeout=0.5)
            if message is None:
                continue
            sample = MavlinkSample(monotonic_s=self._elapsed())
            message_type = message.get_type()
            if message_type == "HEARTBEAT":
                custom = int(message.custom_mode)
                sample.armed = bool(message.base_mode & mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED)
                sample.flight_mode = mode_map.get(custom, f"custom_{custom}")
                if sample.flight_mode == "rtl" or "rtl" in (sample.flight_mode or ""):
                    sample.flight_mode = "return_to_launch"
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
                text = str(message.text).strip("\x00")
                sample.statustext = text
                if "geofence" in text.lower():
                    sample.geofence_breach = True
            elif message_type == "FENCE_STATUS":
                breached = int(message.breach_status) != 0
                sample.geofence_breach = breached
            else:
                continue
            if sample.geofence_breach and self.geofence_breach_at_s is None:
                self.geofence_breach_at_s = sample.monotonic_s
            self.samples.append(sample)


_PROC_UDP = re.compile(
    r"^\s*\d+:\s+[0-9A-F]{8}:(?P<port>[0-9A-F]{4})\s+(?P<addr>[0-9A-F]{8})", re.MULTILINE
)


def relay_uses_loopback_udp_only(
    process: object,
    *,
    ingest_port: int,
) -> bool:
    """Fail closed when relay socket layout cannot be confirmed."""
    pid = int(getattr(process, "pid", 0))
    if pid <= 0:
        return False
    try:
        import psutil
    except ModuleNotFoundError:
        return _relay_loopback_via_proc_net(pid, ingest_port=ingest_port)
    if getattr(process, "poll", lambda: None)() is not None:
        return False
    root = psutil.Process(pid)
    processes = [root, *root.children(recursive=True)]
    saw_ingest = False
    for candidate in processes:
        for connection in candidate.net_connections(kind="all"):
            if connection.type == 2 and connection.status == "LISTEN":  # SOCK_STREAM LISTEN
                return False
            if connection.type != 1:  # SOCK_DGRAM
                continue
            host = _connection_host(connection.laddr)
            if not host or not _is_loopback(host):
                return False
            port = _connection_port(connection.laddr)
            if port == ingest_port:
                saw_ingest = True
    return saw_ingest


def _relay_loopback_via_proc_net(pid: int, *, ingest_port: int) -> bool:
    path = f"/proc/{pid}/net/udp"
    try:
        text = open(path, encoding="utf-8").read()  # noqa: SIM115
    except OSError:
        return False
    port_hex = f"{ingest_port:04X}"
    saw = False
    for match in _PROC_UDP.finditer(text):
        if match.group("port") != port_hex:
            continue
        addr = match.group("addr")
        if addr.endswith("7F000001") or addr.endswith("0100007F"):
            saw = True
    return saw


def _connection_host(laddr: object) -> str | None:
    host = getattr(laddr, "host", None)
    if host is not None:
        return str(host)
    if isinstance(laddr, tuple) and laddr:
        return str(laddr[0])
    return None


def _connection_port(laddr: object) -> int | None:
    port = getattr(laddr, "port", None)
    if port is not None:
        return int(port)
    if isinstance(laddr, tuple) and len(laddr) > 1:
        return int(laddr[1])
    return None


def _is_loopback(host: str) -> bool:
    import ipaddress

    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"127.0.0.1", "::1", "localhost"}