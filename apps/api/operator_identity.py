"""Resolve the control-room operator from the transport boundary, not from Host."""

from __future__ import annotations

import ipaddress
import os

from starlette.requests import Request

_PROXY_IDENTITY_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "forwarded",
        "x-real-ip",
        "x-forwarded-host",
        "x-forwarded-proto",
        "via",
    }
)

_LOCAL_HOST_HEADER_NAMES = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})
_TEST_HOST_HEADER_NAMES = frozenset({"testclient", "testserver"})
_TEST_MODE_ENV = "BYTEWOLF_OPERATOR_IDENTITY_TEST_MODE"


class OperatorIdentityError(RuntimeError):
    """Raised when the request cannot be attributed to an authorized operator."""


def _test_mode_enabled() -> bool:
    return os.environ.get(_TEST_MODE_ENV, "").strip().lower() == "true"


def has_proxy_identity_headers(request: Request) -> bool:
    """Proxy headers are present even when empty — treat as non-local."""
    for name in request.headers:
        if name.lower() in _PROXY_IDENTITY_HEADERS:
            return True
    return False


def peer_host(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def _peer_is_loopback(request: Request) -> bool:
    host = peer_host(request)
    if host is None:
        return False
    if _test_mode_enabled() and host in _TEST_HOST_HEADER_NAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        return False
    if addr.is_loopback:
        return True
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        return addr.ipv4_mapped.is_loopback
    return False


def _normalize_host_header(request: Request) -> str:
    raw = (request.headers.get("host") or "").strip()
    if not raw:
        return ""
    if raw.startswith("[") and "]" in raw:
        closing = raw.index("]")
        return raw[: closing + 1].lower()
    return raw.split(":", 1)[0].strip().lower()


def host_header_allowed_for_local(request: Request) -> bool:
    """DNS rebinding guard: loopback peers must present an allowed Host header."""
    hostname = _normalize_host_header(request)
    if not hostname:
        return False
    if hostname in _LOCAL_HOST_HEADER_NAMES:
        return True
    if _test_mode_enabled() and hostname in _TEST_HOST_HEADER_NAMES:
        return True
    return False


def is_local_operator(request: Request) -> bool:
    """True only for direct loopback traffic, allowed Host, and no proxy headers."""
    return (
        _peer_is_loopback(request)
        and host_header_allowed_for_local(request)
        and not has_proxy_identity_headers(request)
    )


def resolve_operator(request: Request) -> str:
    """Return the operator login, or ``local`` for verified loopback traffic."""
    if is_local_operator(request):
        return "local"
    login = request.headers.get("Tailscale-User-Login", "").strip().lower()
    allowed = {
        item.strip().lower()
        for item in os.environ.get("BYTEWOLF_TAILSCALE_OPERATORS", "").split(",")
        if item.strip()
    }
    host_header = _normalize_host_header(request)
    if not host_header.endswith(".ts.net") or not login:
        raise OperatorIdentityError("Tailscale Serve identity required")
    if login not in allowed:
        raise OperatorIdentityError("Tailnet user is not an authorized operator")
    return login