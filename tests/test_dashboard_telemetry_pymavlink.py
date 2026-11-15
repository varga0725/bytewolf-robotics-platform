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
from urllib.parse import urlparse

from brain.cli import dashboard_telemetry
from brain.telemetry.loopback_endpoint import LoopbackEndpointError, require_loopback_udpin_endpoint


class LoopbackEndpointTests(unittest.TestCase):
    def test_loopback_udpin_is_accepted(self) -> None:
        require_loopback_udpin_endpoint("udpin://127.0.0.1:14540")

    def test_localhost_udpin_is_accepted(self) -> None:
        require_loopback_udpin_endpoint("udpin://localhost:14540")

    def test_documentation_range_host_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://192.0.2.1:14540")

    def test_documentation_range_host_is_rejected_in_cli(self) -> None:
        completed = subprocess.run(
            [
                sys.executable,
                "-m",
                "brain.cli.dashboard_telemetry",
                "--backend",
                "pymavlink",
                "--endpoint",
                "udpin://203.0.113.1:14540",
                "--seconds",
                "0.1",
            ],
            capture_output=True,
            text=True,
            cwd=Path(__file__).resolve().parents[1],
        )
        self.assertEqual(completed.returncode, 2)

    def test_non_udpin_scheme_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("tcp://127.0.0.1:5760")

    def test_ipv6_literal_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://[::1]:14540")

    def test_userinfo_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://user@127.0.0.1:14540")

    def test_path_suffix_is_rejected(self) -> None:
        with self.assertRaises(LoopbackEndpointError):
            require_loopback_udpin_endpoint("udpin://127.0.0.1:14540/extra")


class DashboardTelemetryBackendTests(unittest.TestCase):
    def test_pymavlink_is_the_default_backend(self) -> None:
        parsed = dashboard_telemetry.parse_arguments([])
        self.assertEqual(parsed.backend, "pymavlink")
        self.assertEqual(parsed.stale_timeout, 10.0)
        self.assertEqual(parsed.write_interval, 0.2)


def _laddr_host(connection: object) -> str:
    if connection.laddr is None:
        return ""
    if hasattr(connection.laddr, "ip"):
        return str(connection.laddr.ip)
    return str(connection.laddr[0])


def _laddr_port(connection: object) -> int | None:
    if connection.laddr is None:
        return None
    if hasattr(connection.laddr, "port"):
        return int(connection.laddr.port)
    return int(connection.laddr[1])


def _inspect_relay_connections(process: object, *, ingest_port: int) -> None:
    import psutil as psutil_module

    if process.poll() is not None:
        raise AssertionError(f"relay exited early with code {process.returncode}")

    root = psutil_module.Process(process.pid)
    processes = [root, *root.children(recursive=True)]
    connections: list[object] = []
    for candidate in processes:
        connections.extend(candidate.net_connections(kind="all"))

    loopback_udp_on_port = False
    for connection in connections:
        if connection.type == socket.SOCK_STREAM:
            if connection.status == psutil_module.CONN_LISTEN:
                raise AssertionError(f"TCP listener is not allowed: {connection}")
        if connection.type != socket.SOCK_DGRAM:
            continue
        host = _laddr_host(connection)
        if not host:
            continue
        if not ipaddress.ip_address(host).is_loopback:
            raise AssertionError(f"non-loopback UDP socket is not allowed: {connection}")
        port = _laddr_port(connection)
        if port == ingest_port:
            loopback_udp_on_port = True

    if not loopback_udp_on_port:
        raise AssertionError(
            f"expected a loopback UDP ingest socket on port {ingest_port}; saw: {connections}"
        )


@unittest.skipUnless(os.environ.get("BYTEWOLF_SITL_CONNECTION"), "Set BYTEWOLF_SITL_CONNECTION for SITL relay test")
class DashboardTelemetryRelaySitlTests(unittest.TestCase):
    def test_relay_process_has_no_non_loopback_or_tcp_listeners(self) -> None:
        try:
            import psutil  # noqa: F401
        except ModuleNotFoundError:
            self.skipTest("psutil is not installed")

        connection = os.environ["BYTEWOLF_SITL_CONNECTION"]
        require_loopback_udpin_endpoint(connection)
        ingest_port = urlparse(connection).port
        assert ingest_port is not None
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
        _inspect_relay_connections(proc, ingest_port=ingest_port)


if __name__ == "__main__":
    unittest.main()