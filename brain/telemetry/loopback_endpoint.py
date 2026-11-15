"""Loopback-only MAVLink ingest endpoints for read-only telemetry relays."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class LoopbackEndpointError(ValueError):
    """Raised when a telemetry backend is asked to open a non-loopback transport."""


def require_loopback_udpin_endpoint(endpoint: str) -> None:
    """Require ``udpin://`` on a loopback host (127.0.0.0/8 or ``::1``).

    ``localhost`` is accepted when it resolves only to loopback addresses. A
    hostname that resolves to any non-loopback address is rejected, as are
    ``udpout``, ``tcp``, ``tcpin``, ``serial``, and bind-wide hosts such as
    ``0.0.0.0``.
    """

    if not isinstance(endpoint, str) or not endpoint.strip():
        raise LoopbackEndpointError("Vehicle endpoint must be a non-empty string.")
    parsed = urlparse(endpoint.strip())
    if parsed.scheme != "udpin":
        raise LoopbackEndpointError(
            "The pymavlink telemetry backend accepts udpin:// on loopback only; "
            f"got scheme {parsed.scheme!r}."
        )
    if parsed.hostname is None or parsed.port is None:
        raise LoopbackEndpointError("Network endpoint must include a host and port.")
    host = parsed.hostname.strip().lower()
    if host in {"0.0.0.0", "::", "[::]"}:
        raise LoopbackEndpointError("Bind-all hosts are not permitted for telemetry ingest.")
    if _host_is_loopback(host):
        return
    raise LoopbackEndpointError(
        f"Telemetry ingest host {parsed.hostname!r} is not loopback (127.0.0.0/8 or ::1)."
    )


def _host_is_loopback(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return _resolved_addresses_are_loopback(host)
    return address.is_loopback


def _resolved_addresses_are_loopback(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM)
    except socket.gaierror as error:
        raise LoopbackEndpointError(f"Cannot resolve telemetry host {host!r}: {error}") from error
    if not infos:
        raise LoopbackEndpointError(f"Cannot resolve telemetry host {host!r}.")
    for family, _type, _proto, _canonname, sockaddr in infos:
        if family == socket.AF_INET:
            if not ipaddress.IPv4Address(sockaddr[0]).is_loopback:
                return False
        elif family == socket.AF_INET6:
            if not ipaddress.IPv6Address(sockaddr[0]).is_loopback:
                return False
    return True


__all__ = ["LoopbackEndpointError", "require_loopback_udpin_endpoint"]