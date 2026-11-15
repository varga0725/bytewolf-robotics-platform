"""Read-only MAVLink parameter requests for passive telemetry (PARAM_REQUEST_READ only)."""

from __future__ import annotations


def encode_param_id(name: str) -> bytes:
    encoded = name.encode("utf-8")[:16]
    return encoded + b"\x00" * (16 - len(encoded))


def request_param_read(mavlink: object, *, target_system: int, target_component: int, param_name: str) -> None:
    """Send a single PARAM_REQUEST_READ for one PX4 parameter name."""
    param_id = encode_param_id(param_name)
    send = getattr(getattr(mavlink, "mav", None), "param_request_read_send", None)
    if not callable(send):
        return
    send(target_system, target_component, param_id, -1)


__all__ = ["encode_param_id", "request_param_read"]