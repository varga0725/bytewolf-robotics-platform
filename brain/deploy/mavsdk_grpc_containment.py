"""Detect and contain MAVSDK ``mavsdk_server`` gRPC listeners (BYT-73).

The bundled ``mavsdk_server`` binds gRPC to ``0.0.0.0:<port>`` unless a future wheel
adds a bind-host flag (see ``docs/mavsdk-server-grpc-listen-address.md``). Host
firewall drops limit who can reach the MAVSDK gRPC port set; verification matches
that design (rule present; ``0.0.0.0`` bind allowed only when contained).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

# Keep aligned with mission CLIs, scenario runner, and autonomous_approach.
MAVSDK_GRPC_DEFAULT_PORT = 50051
MAVSDK_GRPC_AUTONOMOUS_APPROACH_PORT = 50055
MAVSDK_GRPC_PORT_BASE = 51000
MAVSDK_GRPC_PORT_SPAN = 10000  # inclusive range [51000, 60999]

_FIREWALL_COMMENT = "bytewolf-mavsdk-grpc-loopback"

_IPTABLES_IPV4 = "/usr/sbin/iptables"
_IPTABLES_IPV6 = "/usr/sbin/ip6tables"

_SS_LOCAL = re.compile(
    r"^LISTEN\s+\S+\s+\S+\s+(?P<local>\S+)\s+\S+(?:\s+users:\(\(.*mavsdk_server.*\)\))?$",
    re.MULTILINE,
)


def mavsdk_grpc_dport_spec() -> str:
    """Multiport destination spec for iptables/ip6tables."""
    last = MAVSDK_GRPC_PORT_BASE + MAVSDK_GRPC_PORT_SPAN - 1
    return (
        f"{MAVSDK_GRPC_DEFAULT_PORT},{MAVSDK_GRPC_AUTONOMOUS_APPROACH_PORT},"
        f"{MAVSDK_GRPC_PORT_BASE}:{last}"
    )


def firewall_comment() -> str:
    return _FIREWALL_COMMENT


def is_grpc_port_protected(port: int) -> bool:
    if port == MAVSDK_GRPC_DEFAULT_PORT or port == MAVSDK_GRPC_AUTONOMOUS_APPROACH_PORT:
        return True
    last = MAVSDK_GRPC_PORT_BASE + MAVSDK_GRPC_PORT_SPAN - 1
    return MAVSDK_GRPC_PORT_BASE <= port <= last


def containment_drop_rule_args(*, ipv6: bool = False) -> list[str]:
    """Arguments after ``iptables -C INPUT`` for the containment DROP rule."""
    args = ["!", "-i", "lo"]
    if ipv6:
        args.extend(["!", "-s", "::1/128"])
    args.extend(
        [
            "-p",
            "tcp",
            "-m",
            "conntrack",
            "--ctstate",
            "NEW",
            "-m",
            "multiport",
            "--dports",
            mavsdk_grpc_dport_spec(),
            "-m",
            "comment",
            "--comment",
            firewall_comment(),
            "-j",
            "DROP",
        ]
    )
    return args


def _mavsdk_server_help_text(project_root: Path) -> str | None:
    try:
        import mavsdk
    except ImportError:
        return None
    binary = Path(mavsdk.__file__).resolve().parent / "bin" / "mavsdk_server"
    if not binary.is_file():
        binary = project_root / ".venv/lib/python3.12/site-packages/mavsdk/bin/mavsdk_server"
    if not binary.is_file():
        return None
    completed = subprocess.run(
        [str(binary), "-h"],
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    return (completed.stdout or "") + (completed.stderr or "")


def grpc_bind_loopback_supported(project_root: Path) -> bool:
    """True when the bundled ``mavsdk_server`` documents a gRPC listen-host override."""
    help_text = _mavsdk_server_help_text(project_root)
    if not help_text:
        return False
    lowered = help_text.lower()
    bind_markers = (
        "grpc-host",
        "grpc host",
        "listen-host",
        "listen host",
        "bind-host",
        "bind host",
    )
    return any(marker in lowered for marker in bind_markers)


def containment_rules_present(
    *,
    run_check: Callable[[list[str]], int] | None = None,
) -> tuple[bool, list[str]]:
    """True when IPv4 (and IPv6 when available) containment DROP rules exist."""
    runner = run_check or _default_iptables_check
    missing: list[str] = []
    if runner([_IPTABLES_IPV4, "-C", "INPUT", *containment_drop_rule_args(ipv6=False)]) != 0:
        missing.append("ipv4")
    if os.path.isfile(_IPTABLES_IPV6):
        if runner([_IPTABLES_IPV6, "-C", "INPUT", *containment_drop_rule_args(ipv6=True)]) != 0:
            missing.append("ipv6")
    return (not missing, missing)


def _default_iptables_check(argv: list[str]) -> int:
    prefixed = ["sudo", *argv] if not argv[0].endswith("sudo") else argv
    return subprocess.run(prefixed, capture_output=True, text=True, check=False).returncode


def _parse_local_host(local: str) -> tuple[str, int]:
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


def _iter_mavsdk_listen_rows(ss_text: str) -> list[str]:
    rows: list[str] = []
    for line in ss_text.splitlines():
        stripped = line.strip()
        if not stripped or "mavsdk_server" not in stripped:
            continue
        if not stripped.startswith("LISTEN"):
            rows.append(stripped)
            continue
        rows.append(stripped)
    return rows


def verify_posture(
    ss_text: str,
    *,
    rules_present: bool,
    bind_loopback_supported: bool,
) -> list[str]:
    """Return verification violations (empty means acceptable posture)."""
    violations: list[str] = []

    if not bind_loopback_supported and not rules_present:
        violations.append(
            "host firewall containment rule is missing "
            f"(comment {firewall_comment()!r}); "
            "mavsdk_server cannot bind gRPC to loopback on the pinned MAVSDK wheel"
        )

    for line in _iter_mavsdk_listen_rows(ss_text):
        if "users:" not in line:
            violations.append(f"mavsdk_server listener missing process metadata: {line!r}")
            continue
        if "mavsdk_server" not in line.split("users:", maxsplit=1)[-1]:
            violations.append(f"mavsdk_server listener process name not confirmed: {line!r}")
            continue

        match = _SS_LOCAL.match(line)
        if match is None:
            violations.append(f"unparseable mavsdk_server ss listener row: {line!r}")
            continue

        local = match.group("local")
        try:
            host, port = _parse_local_host(local)
        except ValueError:
            violations.append(f"mavsdk_server listener with unparseable local address {local!r}")
            continue

        if not is_grpc_port_protected(port):
            violations.append(
                f"mavsdk_server gRPC port {port} is outside the protected firewall range "
                f"({mavsdk_grpc_dport_spec()})"
            )

        loopback_bind = _host_is_loopback_listen(host)
        if bind_loopback_supported:
            if not loopback_bind:
                violations.append(
                    f"mavsdk_server must listen on loopback when bind is supported; saw {local}"
                )
        elif not loopback_bind and not rules_present:
            violations.append(
                f"mavsdk_server listens on uncontained non-loopback {local} "
                "(apply deploy/systemd/apply-mavsdk-grpc-containment.sh)"
            )

    return violations


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path("/home/bytewolf/bytewolf-robotics/platform"),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    verify = sub.add_parser("verify-posture", help="verify mavsdk gRPC containment vs ss listeners")
    verify.add_argument(
        "ss_text",
        nargs="?",
        default=None,
        help="ss -ltnp output (default: read stdin)",
    )
    verify.add_argument(
        "--rules-present",
        choices=("yes", "no", "check"),
        default="check",
        help="whether firewall rules are present (default: probe with sudo iptables -C)",
    )

    args = parser.parse_args(argv)
    if args.command == "verify-posture":
        text = sys.stdin.read() if args.ss_text is None else args.ss_text
        bind_supported = grpc_bind_loopback_supported(args.project_root)
        if args.rules_present == "check":
            rules_ok, missing = containment_rules_present()
            rules_present = rules_ok
            if not rules_ok and not bind_supported:
                print(
                    f"missing containment rules for: {', '.join(missing)}",
                    file=sys.stderr,
                )
        else:
            rules_present = args.rules_present == "yes"

        violations = verify_posture(
            text,
            rules_present=rules_present,
            bind_loopback_supported=bind_supported,
        )
        if violations:
            print(
                "mavsdk_server gRPC containment check failed:\n"
                + "\n".join(f"  - {item}" for item in violations),
                file=sys.stderr,
            )
            return 1
        mode = "loopback bind" if bind_supported else "firewall containment"
        print(f"mavsdk_server gRPC posture ok ({mode})")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(_main())