"""Loopback-only MAVLink ingest endpoints for read-only telemetry relays."""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class LoopbackEndpointError(ValueError):
    """Raised when a telemetry backend is asked to open a non-loopback transport."""


def require_loopback_udpin_endpoint(endpoint: str) -> None:
    """Require ``udpin://`` on an IPv4 loopback host (127.0.0.0/8).

    ``localhost`` is accepted when it resolves only to IPv4 loopback addresses.
    IPv6 literals (including ``::1``) are rejected because the pymavlink
    connection string normalizer does not form a valid IPv6 ingest URI. URLs
    with userinfo or a path component are rejected.
    """

    if not isinstance(endpoint, str) or not endpoint.strip():
        raise LoopbackEndpointError("Vehicle endpoint must be a non-empty string.")
    parsed = urlparse(endpoint.strip())
    if parsed.scheme != "udpin":
        raise LoopbackEndpointError(
            "The pymavlink telemetry backend accepts udpin:// on loopback only; "
            f"got scheme {parsed.scheme!r}."
        )
    if parsed.username or parsed.password:
        raise LoopbackEndpointError("Vehicle endpoint must not include userinfo.")
    path = parsed.path or ""
    if path not in {"", "/"}:
        raise LoopbackEndpointError("Vehicle endpoint must not include a path component.")
    if parsed.hostname is None or parsed.port is None:
        raise LoopbackEndpointError("Network endpoint must include a host and port.")
    host = parsed.hostname.strip().lower()
    if host in {"0.0.0.0", "::", "[::]", "::1"}:
        raise LoopbackEndpointError(
            "IPv6 and bind-all hosts are not supported for pymavlink telemetry ingest."
        )
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if not _resolved_ipv4_addresses_are_loopback(host):
            raise LoopbackEndpointError(
                f"Telemetry ingest host {parsed.hostname!r} is not IPv4 loopback (127.0.0.0/8)."
            ) from None
        return
    if address.version != 4 or not address.is_loopback:
        raise LoopbackEndpointError(
            f"Telemetry ingest host {parsed.hostname!r} is not IPv4 loopback (127.0.0.0/8)."
        )


def _resolved_ipv4_addresses_are_loopback(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_DGRAM, family=socket.AF_INET)
    except socket.gaierror as error:
        raise LoopbackEndpointError(f"Cannot resolve telemetry host {host!r}: {error}") from error
    if not infos:
        raise LoopbackEndpointError(f"Cannot resolve telemetry host {host!r}.")
    for _family, _type, _proto, _canonname, sockaddr in infos:
        if not ipaddress.IPv4Address(sockaddr[0]).is_loopback:
            return False
    return True


__all__ = ["LoopbackEndpointError", "require_loopback_udpin_endpoint"]