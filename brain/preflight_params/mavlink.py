"""MAVLink parameter download via pymavlink (read-only telemetry only)."""

from __future__ import annotations

import time
from dataclasses import dataclass
from urllib.parse import ParseResult, urlparse

from brain.preflight_params.param_value import ParamValue, decode_mavlink_param_value


class MavlinkReadError(RuntimeError):
    """Connection or parameter download failure."""


_DEFAULT_SERIAL_BAUD = 57600


@dataclass(frozen=True)
class MavlinkConnectionTarget:
    """First argument and kwargs for ``pymavlink.mavutil.mavlink_connection``."""

    device: str
    kwargs: dict[str, object]


def _serial_device_and_baud(parsed: ParseResult) -> tuple[str, int]:
    if parsed.path:
        path = parsed.path
        if path.startswith("/") and ":" in path:
            device, suffix = path.rsplit(":", 1)
            if suffix.isdigit():
                return device, int(suffix)
        if parsed.port is not None:
            return path, parsed.port
        return path, _DEFAULT_SERIAL_BAUD
    if parsed.port is not None and parsed.netloc:
        device = parsed.netloc.rsplit(":", 1)[0]
        return device, parsed.port
    if parsed.hostname:
        baud = parsed.port if parsed.port is not None else _DEFAULT_SERIAL_BAUD
        return parsed.hostname, baud
    if parsed.netloc:
        if ":" in parsed.netloc:
            device, suffix = parsed.netloc.rsplit(":", 1)
            if suffix.isdigit():
                return device, int(suffix)
        return parsed.netloc, parsed.port if parsed.port is not None else _DEFAULT_SERIAL_BAUD
    raise MavlinkReadError(f"Cannot parse serial MAVLink URI: {parsed.geturl()!r}")


def resolve_mavlink_connection(connection: str) -> MavlinkConnectionTarget:
    """Convert MAVSDK-style URIs to a pymavlink connection target."""

    if "://" not in connection:
        return MavlinkConnectionTarget(connection, {})
    parsed = urlparse(connection)
    scheme = parsed.scheme
    if scheme in {"udp", "udpin", "udpout", "tcp", "tcpin", "tcpout"}:
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 14550
        return MavlinkConnectionTarget(f"{scheme}:{host}:{port}", {})
    if scheme == "serial":
        device, baud = _serial_device_and_baud(parsed)
        return MavlinkConnectionTarget(device, {"baud": baud})
    raise MavlinkReadError(f"Unsupported MAVLink connection scheme: {scheme!r}")


def normalize_connection_url(connection: str) -> str:
    """Return a logging-friendly connection description."""

    target = resolve_mavlink_connection(connection)
    baud = target.kwargs.get("baud")
    if baud is not None:
        return f"serial:{target.device}@{baud}"
    return target.device


def decode_param_message(param_value: float, param_type: int) -> ParamValue:
    """Decode one ``PARAM_VALUE`` payload (exposed for unit tests)."""

    return decode_mavlink_param_value(param_value, param_type)


def fetch_params_over_mavlink(connection: str, *, timeout_s: float) -> dict[str, ParamValue]:
    """Request PARAM_VALUE stream until PX4 reports the full set."""

    try:
        from pymavlink import mavutil
    except ModuleNotFoundError as error:
        raise MavlinkReadError(
            "pymavlink is not installed. Run: .venv/bin/pip install -r requirements.txt"
        ) from error

    target = resolve_mavlink_connection(connection)
    deadline = time.monotonic() + timeout_s
    mavlink = mavutil.mavlink_connection(target.device, **target.kwargs)
    try:
        if mavlink.wait_heartbeat(timeout=timeout_s) is None:
            raise MavlinkReadError(f"No MAVLink heartbeat on {connection!r} within {timeout_s:.1f} s")

        target_system = mavlink.target_system
        target_component = mavlink.target_component
        mavlink.mav.param_request_list_send(target_system, target_component)

        params: dict[str, ParamValue] = {}
        expected_count: int | None = None

        while time.monotonic() < deadline:
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                break
            message = mavlink.recv_match(type="PARAM_VALUE", blocking=True, timeout=min(1.0, remaining))
            if message is None:
                if expected_count is not None and len(params) >= expected_count:
                    break
                continue
            name = message.param_id.rstrip("\x00")
            params[name] = decode_mavlink_param_value(float(message.param_value), int(message.param_type))
            expected_count = int(message.param_count)
            if len(params) >= expected_count:
                break

        if not params:
            raise MavlinkReadError(f"No parameters received from {connection!r}")
        if expected_count is not None and len(params) < expected_count:
            raise MavlinkReadError(
                f"Only {len(params)}/{expected_count} parameters received before timeout ({timeout_s:.1f} s)"
            )
        return params
    finally:
        close = getattr(mavlink, "close", None)
        if callable(close):
            close()