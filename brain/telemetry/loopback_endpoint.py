"""Loopback-only MAVLink ingest endpoints for read-only telemetry relays."""

from __future__ import annotations

from urllib.parse import urlparse

from brain.safety.connection_policy import ConnectionPolicyError, require_local_sitl_udp_endpoint


class LoopbackEndpointError(ConnectionPolicyError):
    """Raised when a telemetry backend is asked to open a non-loopback transport."""


def require_loopback_udpin_endpoint(endpoint: str) -> None:
    """Require ``udpin://127.0.0.1:<port>`` (see ``connection_policy``)."""

    try:
        require_local_sitl_udp_endpoint(endpoint, schemes=frozenset({"udpin"}))
    except ConnectionPolicyError as error:
        if error.args and "scheme must be" in str(error.args[0]):
            scheme = urlparse(endpoint.strip()).scheme or "<missing>"
            raise LoopbackEndpointError(
                "The pymavlink telemetry backend accepts udpin:// on loopback only; "
                f"got scheme {scheme!r}."
            ) from error
        raise LoopbackEndpointError(str(error)) from error


__all__ = ["LoopbackEndpointError", "require_loopback_udpin_endpoint"]