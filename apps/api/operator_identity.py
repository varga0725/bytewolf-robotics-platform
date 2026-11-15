"""Resolve the control-room operator from the transport boundary, not from Host."""

from __future__ import annotations

import os

from starlette.requests import Request

_PROXY_IDENTITY_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "forwarded",
        "x-real-ip",
    }
)


class OperatorIdentityError(RuntimeError):
    """Raised when the request cannot be attributed to an authorized operator."""


def has_proxy_identity_headers(request: Request) -> bool:
    for name in request.headers:
        if name.lower() in _PROXY_IDENTITY_HEADERS and request.headers[name].strip():
            return True
    return False


def peer_host(request: Request) -> str | None:
    if request.client is None:
        return None
    return request.client.host


def is_loopback_peer(request: Request) -> bool:
    host = peer_host(request)
    return host in {"127.0.0.1", "::1", "testclient"}


def is_local_operator(request: Request) -> bool:
    """True only for direct loopback traffic with no proxy identity headers."""
    return is_loopback_peer(request) and not has_proxy_identity_headers(request)


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
    host_header = (request.headers.get("host") or "").split(":", 1)[0].strip().lower()
    if not host_header.endswith(".ts.net") or not login:
        raise OperatorIdentityError("Tailscale Serve identity required")
    if login not in allowed:
        raise OperatorIdentityError("Tailnet user is not an authorized operator")
    return login