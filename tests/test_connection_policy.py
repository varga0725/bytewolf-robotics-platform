"""Unit tests for the shared loopback UDP connection policy."""

from __future__ import annotations

import unittest

from brain.safety.connection_policy import ConnectionPolicyError, require_local_sitl_udp_endpoint


class ConnectionPolicyAcceptanceTests(unittest.TestCase):
    def test_udpin_loopback_is_accepted(self) -> None:
        require_local_sitl_udp_endpoint("udpin://127.0.0.1:14540")

    def test_udpout_loopback_is_accepted(self) -> None:
        require_local_sitl_udp_endpoint("udpout://127.0.0.1:14550")


class ConnectionPolicyRejectionTests(unittest.TestCase):
    def test_empty_endpoint_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("")

    def test_whitespace_only_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("   ")

    def test_bind_all_host_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://0.0.0.0:14540")

    def test_localhost_name_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://localhost:14540")

    def test_ipv6_loopback_literal_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://[::1]:14540")

    def test_ipv6_loopback_unbracketed_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://::1:14540")

    def test_remote_documentation_host_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://203.0.113.5:14540")

    def test_serial_scheme_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("serial:///dev/ttyACM0")

    def test_tcp_scheme_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("tcp://127.0.0.1:5760")

    def test_tcpin_scheme_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("tcpin://127.0.0.1:5760")

    def test_userinfo_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://user@127.0.0.1:14540")

    def test_path_suffix_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://127.0.0.1:14540/extra")

    def test_missing_port_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://127.0.0.1")

    def test_missing_host_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://:14540")

    def test_port_zero_is_rejected(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpin://127.0.0.1:0")

    def test_udpin_only_subset_rejects_udpout(self) -> None:
        with self.assertRaises(ConnectionPolicyError):
            require_local_sitl_udp_endpoint("udpout://127.0.0.1:14540", schemes=frozenset({"udpin"}))


if __name__ == "__main__":
    unittest.main()