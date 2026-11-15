"""Local operator detection uses the socket peer and Host allowlist."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from starlette.requests import Request

from apps.api.operator_identity import (
    has_proxy_identity_headers,
    host_header_allowed_for_local,
    is_local_operator,
    resolve_operator,
)
from apps.api.server import create_app


def _request(
    *,
    host: str = "127.0.0.1",
    client_host: str = "127.0.0.1",
    headers: list[tuple[str, str]] | None = None,
) -> Request:
    header_items = list(headers or [])
    if not any(name.lower() == "host" for name, _ in header_items):
        header_items.append(("Host", host if ":" in host else f"{host}:8080"))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in header_items],
        "client": (client_host, 12345),
        "server": (host, 8080),
    }
    return Request(scope)


class OperatorIdentityTests(unittest.TestCase):
    def test_loopback_peer_with_allowed_host_is_local(self) -> None:
        request = _request(client_host="127.0.0.1", headers=[("Host", "127.0.0.1:8080")])
        self.assertTrue(is_local_operator(request))
        self.assertEqual(resolve_operator(request), "local")

    def test_loopback_peer_with_foreign_host_is_not_local(self) -> None:
        request = _request(client_host="127.0.0.1", headers=[("Host", "evil.example.com")])
        self.assertFalse(is_local_operator(request))
        with self.assertRaisesRegex(Exception, "Tailscale"):
            resolve_operator(request)

    def test_remote_peer_is_not_local_even_with_localhost_host(self) -> None:
        request = _request(client_host="203.0.113.10", headers=[("Host", "localhost")])
        self.assertFalse(is_local_operator(request))

    def test_ipv6_loopback_peer_and_host(self) -> None:
        request = _request(client_host="::1", headers=[("Host", "[::1]:8080")])
        self.assertTrue(host_header_allowed_for_local(request))
        self.assertTrue(is_local_operator(request))

    def test_mapped_ipv4_loopback_peer(self) -> None:
        request = _request(client_host="::ffff:127.0.0.1", headers=[("Host", "127.0.0.1:8080")])
        self.assertTrue(is_local_operator(request))

    def test_proxy_headers_block_local_on_loopback(self) -> None:
        request = _request(headers=[("Host", "127.0.0.1"), ("X-Forwarded-For", "203.0.113.10")])
        self.assertTrue(has_proxy_identity_headers(request))
        self.assertFalse(is_local_operator(request))

    def test_empty_proxy_header_blocks_local(self) -> None:
        request = _request(headers=[("Host", "127.0.0.1"), ("Via", "")])
        self.assertFalse(is_local_operator(request))


class OperatorIdentityHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory

        self.directory = TemporaryDirectory()
        telemetry = Path(self.directory.name) / "telemetry.json"
        telemetry.write_text(json.dumps({"in_air": False}), encoding="utf-8")
        self._patch = patch.dict(
            os.environ,
            {"BYTEWOLF_OPERATOR_IDENTITY_TEST_MODE": "true"},
            clear=False,
        )
        self._patch.start()
        self.client = TestClient(create_app(telemetry))

    def tearDown(self) -> None:
        self._patch.stop()
        self.directory.cleanup()

    def test_foreign_host_on_loopback_peer_is_rejected(self) -> None:
        response = self.client.get("/api/v1/auth/session", headers={"Host": "evil.example.com"})
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()