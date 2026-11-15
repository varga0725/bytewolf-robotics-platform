"""Unit tests for mavsdk_server gRPC firewall containment (BYT-73)."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from brain.deploy.mavsdk_grpc_containment import (
    MAVSDK_GRPC_AUTONOMOUS_APPROACH_PORT,
    MAVSDK_GRPC_DEFAULT_PORT,
    MAVSDK_GRPC_PORT_BASE,
    MAVSDK_GRPC_PORT_SPAN,
    _main,
    containment_drop_rule_args,
    containment_rules_present,
    firewall_comment,
    grpc_bind_loopback_supported,
    is_grpc_port_protected,
    mavsdk_grpc_dport_spec,
    verify_posture,
)


class MavsdkGrpcContainmentTests(unittest.TestCase):
    def test_dport_spec_includes_default_scenario_and_autonomous_ports(self) -> None:
        last = MAVSDK_GRPC_PORT_BASE + MAVSDK_GRPC_PORT_SPAN - 1
        self.assertEqual(
            mavsdk_grpc_dport_spec(),
            f"{MAVSDK_GRPC_DEFAULT_PORT},{MAVSDK_GRPC_AUTONOMOUS_APPROACH_PORT},{MAVSDK_GRPC_PORT_BASE}:{last}",
        )
        self.assertTrue(is_grpc_port_protected(50051))
        self.assertTrue(is_grpc_port_protected(50055))
        self.assertTrue(is_grpc_port_protected(51001))
        self.assertFalse(is_grpc_port_protected(49999))
        self.assertFalse(is_grpc_port_protected(61000))

    def test_containment_rule_args_use_conntrack_new_and_skip_loopback_interface(self) -> None:
        ipv4 = containment_drop_rule_args(ipv6=False)
        self.assertEqual(ipv4[:4], ["!", "-i", "lo", "-p"])
        self.assertIn("conntrack", ipv4)
        self.assertIn("NEW", ipv4)
        self.assertIn(mavsdk_grpc_dport_spec(), ipv4)
        self.assertIn(firewall_comment(), ipv4)

        ipv6 = containment_drop_rule_args(ipv6=True)
        self.assertEqual(ipv6[:8], ["!", "-i", "lo", "!", "-s", "::1/128", "-p", "tcp"])
        self.assertIn("--ctstate", ipv6)

    def test_containment_rules_present_uses_sudo_check_runner(self) -> None:
        calls: list[list[str]] = []

        def fake_check(argv: list[str]) -> int:
            calls.append(argv)
            return 0

        ok, missing = containment_rules_present(run_check=fake_check)
        self.assertTrue(ok)
        self.assertEqual(missing, [])
        self.assertGreaterEqual(len(calls), 1)
        drop = containment_drop_rule_args(ipv6=False)
        self.assertEqual(calls[0], ["/usr/sbin/iptables", "-C", "INPUT", *drop])

    def test_wide_bind_allowed_only_when_rules_present(self) -> None:
        exposed = (
            "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        self.assertEqual(
            verify_posture(exposed, rules_present=False, bind_loopback_supported=False),
            [
                "host firewall containment rule is missing "
                f"(comment {firewall_comment()!r}); "
                "mavsdk_server cannot bind gRPC to loopback on the pinned MAVSDK wheel",
                "mavsdk_server listens on uncontained non-loopback 0.0.0.0:50051 "
                "(apply deploy/systemd/apply-mavsdk-grpc-containment.sh)",
            ],
        )
        self.assertEqual(
            verify_posture(exposed, rules_present=True, bind_loopback_supported=False),
            [],
        )

    def test_loopback_bind_required_when_supported(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        violations = verify_posture(fixture, rules_present=True, bind_loopback_supported=True)
        self.assertEqual(len(violations), 1)
        self.assertIn("loopback", violations[0])

    def test_unprotected_mavsdk_port_fails(self) -> None:
        fixture = (
            "LISTEN 0 4096 127.0.0.1:49999 0.0.0.0:* "
            'users:(("mavsdk_server",pid=1,fd=2))\n'
        )
        violations = verify_posture(fixture, rules_present=True, bind_loopback_supported=False)
        self.assertEqual(len(violations), 1)
        self.assertIn("outside the protected firewall range", violations[0])

    def test_fail_closed_on_unparseable_mavsdk_row(self) -> None:
        fixture = 'LISTEN 0 4096 weird 0.0.0.0:* users:(("mavsdk_server",pid=1,fd=2))\n'
        violations = verify_posture(fixture, rules_present=True, bind_loopback_supported=False)
        self.assertEqual(len(violations), 1)
        self.assertIn("unparseable", violations[0])

    def test_fail_closed_when_process_metadata_missing(self) -> None:
        fixture = "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* pid 42 cmd/mavsdk_server\n"
        violations = verify_posture(fixture, rules_present=True, bind_loopback_supported=False)
        self.assertTrue(any("missing process metadata" in item for item in violations))

    def test_grpc_bind_loopback_not_supported_on_pinned_wheel(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertFalse(grpc_bind_loopback_supported(root))

    def test_cli_verify_posture_with_rules_flag(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:50055 0.0.0.0:* "
            'users:(("mavsdk_server",pid=42,fd=7))\n'
        )
        root = str(Path(__file__).resolve().parents[1])
        self.assertEqual(
            _main(["--project-root", root, "verify-posture", "--rules-present", "yes", fixture]),
            0,
        )

    def test_apply_script_uses_sudo_check_and_conntrack_new(self) -> None:
        root = Path(__file__).resolve().parents[1]
        script = (root / "deploy/systemd/apply-mavsdk-grpc-containment.sh").read_text(encoding="utf-8")
        self.assertIn("sudo", script)
        self.assertIn("-C INPUT", script)
        self.assertIn("conntrack", script)
        self.assertIn("--ctstate NEW", script)
        self.assertIn("! -i lo", script)

    def test_cli_fails_without_rules_when_mavsdk_wide_bind(self) -> None:
        fixture = (
            "LISTEN 0 4096 0.0.0.0:50051 0.0.0.0:* "
            'users:(("mavsdk_server",pid=99,fd=4))\n'
        )
        stderr = io.StringIO()
        root = str(Path(__file__).resolve().parents[1])
        with redirect_stderr(stderr):
            code = _main(["--project-root", root, "verify-posture", "--rules-present", "no", fixture])
        self.assertEqual(code, 1)
        self.assertIn("containment", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()