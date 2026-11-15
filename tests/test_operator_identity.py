"""Local operator detection uses the socket peer, not the Host header."""

from __future__ import annotations

import unittest

from starlette.requests import Request

from apps.api.operator_identity import has_proxy_identity_headers, is_local_operator, resolve_operator


def _request(*, host: str = "127.0.0.1", client_host: str = "127.0.0.1", headers: list[tuple[str, str]] | None = None) -> Request:
    header_items = headers or []
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
    def test_loopback_peer_without_proxy_headers_is_local(self) -> None:
        request = _request()
        self.assertTrue(is_local_operator(request))
        self.assertEqual(resolve_operator(request), "local")

    def test_host_header_spoof_does_not_make_a_remote_peer_local(self) -> None:
        request = _request(host="localhost", client_host="203.0.113.10")
        self.assertFalse(is_local_operator(request))
        with self.assertRaisesRegex(Exception, "Tailscale"):
            resolve_operator(request)

    def test_proxy_headers_block_local_on_loopback(self) -> None:
        request = _request(headers=[("X-Forwarded-For", "203.0.113.10")])
        self.assertTrue(has_proxy_identity_headers(request))
        self.assertFalse(is_local_operator(request))


if __name__ == "__main__":
    unittest.main()