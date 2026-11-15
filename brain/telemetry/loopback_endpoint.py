"""Loopback-only MAVLink ingest endpoints for read-only telemetry relays."""

from __future__ import annotations

from brain.safety.connection_policy import (
    ConnectionPolicyError,
    ConnectionPolicySchemeError,
    require_local_sitl_udp_endpoint,
)


class LoopbackEndpointError(ConnectionPolicyError):
    """Raised when a telemetry backend is asked to open a non-loopback transport."""


def require_loopback_udpin_endpoint(endpoint: str) -> None:
    """Require ``udpin://127.0.0.1:<port>`` (see ``connection_policy``)."""

    try:
        require_local_sitl_udp_endpoint(endpoint, schemes=frozenset({"udpin"}))
    except ConnectionPolicySchemeError as error:
        raise LoopbackEndpointError(
            "The pymavlink telemetry backend accepts udpin:// on loopback only; "
            f"got scheme {error.wrong_scheme!r}."
        ) from error
    except ConnectionPolicyError as error:
        raise LoopbackEndpointError(str(error)) from error


__all__ = ["LoopbackEndpointError", "require_loopback_udpin_endpoint"]