"""Single source of truth for local SITL UDP MAVLink connection strings.

Only ``udpin://127.0.0.1:<port>`` and ``udpout://127.0.0.1:<port>`` are accepted.
``localhost``, IPv6 loopback (including ``::1``), bind-all ``0.0.0.0``, serial,
TCP, remote hosts, and URLs with userinfo or path suffixes are rejected everywhere
this policy is applied.

Hostname literals are not resolved: ``localhost`` is refused even when it would
resolve to 127.0.0.0/8, so configuration cannot drift across environments.
"""

from __future__ import annotations

from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"udpin", "udpout"})
_LOOPBACK_IPV4 = "127.0.0.1"


class ConnectionPolicyError(ValueError):
    """Raised when a vehicle endpoint violates the loopback UDP policy."""


def require_local_sitl_udp_endpoint(
    endpoint: str,
    *,
    schemes: frozenset[str] | None = None,
) -> None:
    """Require a loopback UDP endpoint on 127.0.0.1 with an explicit port."""

    allowed = _ALLOWED_SCHEMES if schemes is None else schemes
    if not isinstance(endpoint, str) or not endpoint.strip():
        raise ConnectionPolicyError("Vehicle endpoint must be a non-empty string.")
    parsed = urlparse(endpoint.strip())
    if parsed.scheme not in allowed:
        scheme_list = ", ".join(sorted(allowed))
        raise ConnectionPolicyError(
            f"Vehicle endpoint scheme must be one of {{{scheme_list}}}; got {parsed.scheme!r}."
        )
    if parsed.username or parsed.password:
        raise ConnectionPolicyError("Vehicle endpoint must not include userinfo.")
    path = parsed.path or ""
    if path not in {"", "/"}:
        raise ConnectionPolicyError("Vehicle endpoint must not include a path component.")
    if parsed.hostname is None or parsed.port is None:
        raise ConnectionPolicyError("Network endpoint must include a host and port.")
    host = parsed.hostname.strip().lower()
    if host != _LOOPBACK_IPV4:
        raise ConnectionPolicyError(
            f"Vehicle endpoint host must be {_LOOPBACK_IPV4!r}; got {parsed.hostname!r}."
        )
    if not 1 <= int(parsed.port) <= 65535:
        raise ConnectionPolicyError("Vehicle endpoint port must be between 1 and 65535.")


__all__ = ["ConnectionPolicyError", "require_local_sitl_udp_endpoint"]