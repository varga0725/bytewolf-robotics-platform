"""CLI and integration tests for the pymavlink telemetry backend."""

from __future__ import annotations

import ipaddress
import os
import socket
import subprocess
import sys
import time
import unittest
from pathlib import Path

from brain.cli import dashboard_telemetry
from brain.telemetry.loopback_endpoint import LoopbackEndpointError, require_loopback_udpin_endpoint


class LoopbackEndpointTests(unittest.TestCase):
    def test_loopback_udpin_is_accepted(self) -> None:
        require_loopback_udpin_endpoint("udpin://127.0.0.1:14540")

    def test_localhost_udpin_is_accepted(self) -> None:
        require_loopback_udpin_endpoint("udpin://localhost:14540")

    def test_remote_host_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://192.168.1.1:14540")

    def test_non_udpin_scheme_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("tcp://127.0.0.1:5760")

    def test_cli_exits_2_for_non_loopback_pymavlink_endpoint(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "brain.cli.dashboard_telemetry",
                "--backend",
                "pymavlink",
                "--endpoint",
                "udpin://10.0.0.1:14540",
                "--seconds",
                "0.1",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("loopback", completed.stderr.lower())


class DashboardTelemetryBackendTests(unittest.TestCase):
    def test_pymavlink_is_the_default_backend(self) -> None:
        parsed = dashboard_telemetry.parse_arguments([])
        self.assertEqual(parsed.backend, "pymavlink")
        self.assertEqual(parsed.stale_timeout, 10.0)


def _listener_is_allowed(address: str, family: socket.AddressFamily, sock_type: int) -> bool:
    if sock_type == socket.SOCK_STREAM:
        return False
    if family == socket.AF_INET:
        host = address[0] if isinstance(address, tuple) else address
        return ipaddress.ip_address(host).is_loopback
    if family == socket.AF_INET6:
        host = address[0] if isinstance(address, tuple) else address
        return ipaddress.ip_address(host).is_loopback
    return False


@unittest.skipUnless(os.environ.get("BYTEWOLF_SITL_CONNECTION"), "Set BYTEWOLF_SITL_CONNECTION for SITL relay test")
class DashboardTelemetryRelaySitlTests(unittest.TestCase):
    def test_relay_process_has_no_non_loopback_or_tcp_listeners(self) -> None:
        try:
            import psutil as psutil_module
        except ModuleNotFoundError:
            self.skipTest("psutil is not installed")

        connection = os.environ["BYTEWOLF_SITL_CONNECTION"]
        require_loopback_udpin_endpoint(connection)
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "brain.cli.dashboard_telemetry",
                "--backend",
                "pymavlink",
                "--endpoint",
                connection,
                "--seconds",
                "4",
            ],
            cwd=Path(__file__).resolve().parents[1],
        )
        self.addCleanup(proc.wait, timeout=30)
        time.sleep(1.5)
        try:
            process = psutil_module.Process(proc.pid)
            for child in process.children(recursive=True):
                for connection_info in child.connections(kind="all"):
                    if connection_info.status != psutil_module.CONN_LISTEN:
                        continue
                    if not _listener_is_allowed(
                        connection_info.laddr,
                        connection_info.family,
                        connection_info.type,
                    ):
                        self.fail(f"disallowed listener: {connection_info}")
            for connection_info in process.connections(kind="all"):
                if connection_info.status != psutil_module.CONN_LISTEN:
                    continue
                if not _listener_is_allowed(
                    connection_info.laddr,
                    connection_info.family,
                    connection_info.type,
                ):
                    self.fail(f"disallowed listener on relay: {connection_info}")
        except psutil_module.Error:
            if proc.poll() is not None:
                self.fail(f"relay exited early with code {proc.returncode}")


if __name__ == "__main__":
    unittest.main()