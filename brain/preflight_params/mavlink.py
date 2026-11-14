"""MAVLink parameter download via pymavlink (read-only telemetry only)."""

from __future__ import annotations

import time
from urllib.parse import urlparse

from brain.preflight_params.param_value import ParamValue, decode_mavlink_param_value


class MavlinkReadError(RuntimeError):
    """Connection or parameter download failure."""


def normalize_connection_url(connection: str) -> str:
    """Convert MAVSDK-style URIs to pymavlink connection strings."""

    if "://" not in connection:
        return connection
    parsed = urlparse(connection)
    scheme = parsed.scheme
    if scheme in {"udp", "udpin", "udpout", "tcp", "tcpin", "tcpout"}:
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 14550
        return f"{scheme}:{host}:{port}"
    if scheme == "serial":
        device = parsed.path or parsed.netloc
        device = device.lstrip("/")
        baud = parsed.port or 57600
        return f"{scheme}:{device}:{baud}"
    raise MavlinkReadError(f"Unsupported MAVLink connection scheme: {scheme!r}")


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

    url = normalize_connection_url(connection)
    deadline = time.monotonic() + timeout_s
    mavlink = mavutil.mavlink_connection(url)
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