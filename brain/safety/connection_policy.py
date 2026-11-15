"""Single source of truth for local SITL UDP MAVLink connection strings.

Only ``udpin://127.0.0.1:<port>`` and ``udpout://127.0.0.1:<port>`` are accepted.
``localhost``, IPv6 loopback (including ``::1``), bind-all ``0.0.0.0``, serial,
TCP, remote hosts, and URLs with userinfo or path suffixes are rejected everywhere
this policy is applied.

Hostname literals are not resolved: ``localhost`` is refused even when it would
resolve to 127.0.0.0/8, so configuration cannot drift across environments.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

_ALLOWED_SCHEMES = frozenset({"udpin", "udpout"})
_LOOPBACK_IPV4 = "127.0.0.1"
_PORT_TOKEN_RE = re.compile(r":(\d+)(?:/|$)")


class ConnectionPolicyError(ValueError):
    """Raised when a vehicle endpoint violates the loopback UDP policy."""


class ConnectionPolicySchemeError(ConnectionPolicyError):
    """Raised when the endpoint scheme is outside the allowed UDP set."""

    def __init__(self, message: str, *, wrong_scheme: str, allowed_schemes: frozenset[str]) -> None:
        super().__init__(message)
        self.wrong_scheme = wrong_scheme
        self.allowed_schemes = allowed_schemes


def require_local_sitl_udp_endpoint(
    endpoint: str,
    *,
    schemes: frozenset[str] | None = None,
) -> None:
    """Require a loopback UDP endpoint on 127.0.0.1 with an explicit port."""

    validate_local_sitl_udp_endpoint(endpoint, schemes=schemes)


def validate_local_sitl_udp_endpoint(
    endpoint: str,
    *,
    schemes: frozenset[str] | None = None,
) -> str:
    """Validate and return the canonical ``scheme://127.0.0.1:<port>`` string."""

    allowed = _ALLOWED_SCHEMES if schemes is None else schemes
    if not isinstance(endpoint, str) or not endpoint:
        raise ConnectionPolicyError("Vehicle endpoint must be a non-empty string.")
    if endpoint != endpoint.strip():
        raise ConnectionPolicyError("Vehicle endpoint must not include leading or trailing whitespace.")
    if any(ord(character) < 32 or ord(character) == 127 for character in endpoint):
        raise ConnectionPolicyError("Vehicle endpoint must not include control characters.")
    if "?" in endpoint or "#" in endpoint:
        raise ConnectionPolicyError("Vehicle endpoint must not include a query or fragment.")
    if "://" not in endpoint:
        raise ConnectionPolicyError("Vehicle endpoint must include a scheme and network location.")
    scheme_part, _rest = endpoint.split("://", 1)
    if scheme_part != scheme_part.lower():
        raise ConnectionPolicyError("Vehicle endpoint scheme must be lowercase.")
    parsed = urlparse(endpoint)
    if parsed.scheme not in allowed:
        scheme_list = ", ".join(sorted(allowed))
        raise ConnectionPolicySchemeError(
            f"Vehicle endpoint scheme must be one of {{{scheme_list}}}; got {parsed.scheme!r}.",
            wrong_scheme=parsed.scheme or "",
            allowed_schemes=frozenset(allowed),
        )
    if parsed.username or parsed.password:
        raise ConnectionPolicyError("Vehicle endpoint must not include userinfo.")
    path = parsed.path or ""
    if path not in {"", "/"}:
        raise ConnectionPolicyError("Vehicle endpoint must not include a path component.")
    if parsed.hostname is None:
        raise ConnectionPolicyError("Network endpoint must include a host and port.")
    if "[" in endpoint or parsed.hostname.startswith("[") or ":" in parsed.hostname:
        raise ConnectionPolicyError("IPv6 vehicle endpoints are not supported.")
    host = parsed.hostname
    if host != _LOOPBACK_IPV4:
        raise ConnectionPolicyError(
            f"Vehicle endpoint host must be {_LOOPBACK_IPV4!r}; got {parsed.hostname!r}."
        )
    try:
        parsed_port = parsed.port
    except ValueError as error:
        raise ConnectionPolicyError("Vehicle endpoint port must be a decimal integer.") from error
    port = _validated_port(endpoint, parsed_port)
    return f"{parsed.scheme}://{_LOOPBACK_IPV4}:{port}"


def _validated_port(endpoint: str, parsed_port: int | None) -> int:
    if parsed_port is None:
        raise ConnectionPolicyError("Network endpoint must include a host and port.")
    try:
        port = int(parsed_port)
    except (TypeError, ValueError) as error:
        raise ConnectionPolicyError("Vehicle endpoint port must be a decimal integer.") from error
    if not 1 <= port <= 65535:
        raise ConnectionPolicyError("Vehicle endpoint port must be between 1 and 65535.")
    if match := _PORT_TOKEN_RE.search(endpoint.rsplit("@", 1)[-1]):
        token = match.group(1)
        if len(token) > 1 and token.startswith("0"):
            raise ConnectionPolicyError("Vehicle endpoint port must not use leading zeros.")
        try:
            if int(token, 10) != port:
                raise ConnectionPolicyError("Vehicle endpoint port must be a decimal integer.")
        except ValueError as error:
            raise ConnectionPolicyError("Vehicle endpoint port must be a decimal integer.") from error
    return port


__all__ = [
    "ConnectionPolicyError",
    "ConnectionPolicySchemeError",
    "require_local_sitl_udp_endpoint",
    "validate_local_sitl_udp_endpoint",
]