"""Detect and contain MAVSDK ``mavsdk_server`` gRPC listeners (BYT-73).

The bundled ``mavsdk_server`` binds gRPC to ``0.0.0.0:<port>`` with no supported
host override (see ``docs/mavsdk-server-grpc-listen-address.md``). Containment uses
narrow host firewall drops on the platform's MAVSDK gRPC port range; verification
parses ``ss`` output and rejects any non-loopback listener owned by ``mavsdk_server``.
"""

from __future__ import annotations

import argparse
import ipaddress
import re
import sys

# Keep in sync with ``simulation/scenarios/scenarios.py`` and mission CLI defaults.
MAVSDK_GRPC_DEFAULT_PORT = 50051
MAVSDK_GRPC_PORT_BASE = 51000
MAVSDK_GRPC_PORT_SPAN = 10000  # inclusive range [51000, 60999]

_FIREWALL_COMMENT = "bytewolf-mavsdk-grpc-loopback"

_SS_LOCAL = re.compile(
    r"^LISTEN\s+\S+\s+\S+\s+(?P<local>\S+)\s+\S+(?:\s+.*mavsdk_server.*)?$",
    re.MULTILINE,
)


def mavsdk_grpc_dport_spec() -> str:
    """Multiport destination spec for iptables/ip6tables."""
    last = MAVSDK_GRPC_PORT_BASE + MAVSDK_GRPC_PORT_SPAN - 1
    return f"{MAVSDK_GRPC_DEFAULT_PORT},{MAVSDK_GRPC_PORT_BASE}:{last}"


def firewall_comment() -> str:
    return _FIREWALL_COMMENT


def _parse_local_host(local: str) -> tuple[str, int]:
    """Return (host, port) from an ``ss`` Local Address:Port field."""
    if local.startswith("["):
        host_part, _, port_text = local.rpartition("]:")
        host = host_part.lstrip("[")
        if not port_text.isdigit():
            raise ValueError(f"invalid ss local address: {local!r}")
        return host, int(port_text)
    if local.count(":") == 1:
        host, port_text = local.split(":", 1)
        if not port_text.isdigit():
            raise ValueError(f"invalid ss local address: {local!r}")
        return host, int(port_text)
    raise ValueError(f"invalid ss local address: {local!r}")


def _host_is_loopback_listen(host: str) -> bool:
    if host in {"*", "0.0.0.0"}:
        return False
    if host == "::":
        return False
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback


def non_loopback_mavsdk_listeners(ss_text: str) -> list[str]:
    """Return human-readable violations for ``mavsdk_server`` TCP listeners in ``ss`` output."""
    violations: list[str] = []
    for line in ss_text.splitlines():
        if "mavsdk_server" not in line:
            continue
        match = _SS_LOCAL.match(line.strip())
        if match is None:
            continue
        local = match.group("local")
        try:
            host, port = _parse_local_host(local)
        except ValueError:
            violations.append(f"mavsdk_server listener with unparseable local address {local!r}")
            continue
        if not _host_is_loopback_listen(host):
            violations.append(
                f"mavsdk_server listens on non-loopback {local} "
                f"(gRPC must be reachable only from 127.0.0.0/8; see docs/mavsdk-server-grpc-listen-address.md)"
            )
    return violations


def verify_listeners_from_ss(ss_text: str) -> list[str]:
    """Return violations; empty list means the listener layout is acceptable."""
    return non_loopback_mavsdk_listeners(ss_text)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-listeners", help="fail when ss shows a non-loopback mavsdk_server listener")
    verify.add_argument(
        "ss_text",
        nargs="?",
        default=None,
        help="ss -ltnp output (default: read stdin)",
    )

    args = parser.parse_args(argv)
    if args.command == "verify-listeners":
        text = sys.stdin.read() if args.ss_text is None else args.ss_text
        violations = verify_listeners_from_ss(text)
        if violations:
            print(
                "mavsdk_server gRPC is exposed beyond loopback:\n"
                + "\n".join(f"  - {item}" for item in violations),
                file=sys.stderr,
            )
            return 1
        print("mavsdk_server gRPC listeners: none or loopback-only")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())