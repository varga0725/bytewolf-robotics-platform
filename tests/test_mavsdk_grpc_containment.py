"""Unit tests for mavsdk_server gRPC listener inspection (BYT-73)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr

from brain.deploy.mavsdk_grpc_containment import (
    MAVSDK_GRPC_DEFAULT_PORT,
    MAVSDK_GRPC_PORT_BASE,
    MAVSDK_GRPC_PORT_SPAN,
    _main,
    mavsdk_grpc_dport_spec,
    non_loopback_mavsdk_listeners,
    verify_listeners_from_ss,
)


class MavsdkGrpcContainmentTests(unittest.TestCase):
    def test_dport_spec_matches_scenario_allocator(self) -> None:
        last = MAVSDK_GRPC_PORT_BASE + MAVSDK_GRPC_PORT_SPAN - 1
        self.assertEqual(mavsdk_grpc_dport_spec(), f"{MAVSDK_GRPC_DEFAULT_PORT},{MAVSDK_GRPC_PORT_BASE}:{last}")

    def test_no_mavsdk_lines_passes(self) -> None:
        fixture = "LISTEN 0 4096 127.0.0.1:8080 0.0.0.0:* users:((\"python\",pid=1,fd=3))\n"
        self.assertEqual(non_loopback_mavsdk_listeners(fixture), [])

    def test_loopback_mavsdk_listener_passes(self) -> None:
        fixture = (
            "LISTEN 0 4096 127.0.0.1:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=42,fd=7))\n'
        )
        self.assertEqual(non_loopback_mavsdk_listeners(fixture), [])

    def test_ipv6_loopback_mavsdk_listener_passes(self) -> None:
        fixture = (
            "LISTEN 0 4096 [::1]:51052 0.0.0.0:* "
            'users:(("mavsdk_server",pid=42,fd=7))\n'
        )
        self.assertEqual(non_loopback_mavsdk_listeners(fixture), [])

    def test_all_interfaces_mavsdk_listener_fails(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        violations = non_loopback_mavsdk_listeners(fixture)
        self.assertEqual(len(violations), 1)
        self.assertIn("0.0.0.0:50051", violations[0])

    def test_wildcard_mavsdk_listener_fails(self) -> None:
        fixture = (
            "LISTEN 0 4096 *:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        self.assertEqual(len(non_loopback_mavsdk_listeners(fixture)), 1)

    def test_verify_listeners_reports_violation(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:51001 0.0.0.0:* "
            'users:(("mavsdk_server",pid=1,fd=2))\n'
        )
        self.assertEqual(len(verify_listeners_from_ss(fixture)), 1)

    def test_cli_verify_listeners_reads_fixture(self) -> None:
        fixture = (
            "LISTEN 0 4096 127.0.0.1:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=42,fd=7))\n'
        )
        self.assertEqual(_main(["verify-listeners", fixture]), 0)

    def test_cli_verify_listeners_fails_with_message(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = _main(["verify-listeners", fixture])
        self.assertEqual(code, 1)
        self.assertIn("non-loopback", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()