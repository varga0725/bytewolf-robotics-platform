"""Serve the dashboard locally; this module has no control endpoints."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from apps.dashboard.telemetry import TelemetryFormatError, load_telemetry_snapshot


def create_handler(telemetry_path: Path) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required stdlib handler name
            if self.path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", _DASHBOARD_HTML.encode())
            elif self.path == "/api/telemetry":
                self._send_telemetry()
            else:
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Not found\n")

        def do_POST(self) -> None:  # noqa: N802 - intentional read-only boundary
            self._send(HTTPStatus.METHOD_NOT_ALLOWED, "text/plain; charset=utf-8", b"Read-only dashboard\n")

        def _send_telemetry(self) -> None:
            try:
                snapshot = load_telemetry_snapshot(telemetry_path)
                body = json.dumps(snapshot.as_dict()).encode()
                self._send(HTTPStatus.OK, "application/json", body)
            except TelemetryFormatError as error:
                self._send(HTTPStatus.BAD_REQUEST, "application/json", json.dumps({"error": str(error)}).encode())

        def _send(self, status: HTTPStatus, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return DashboardHandler


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the read-only local telemetry dashboard.")
    parser.add_argument("--telemetry-file", type=Path, required=True)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    host = "127.0.0.1"
    server = ThreadingHTTPServer((host, args.port), create_handler(args.telemetry_file))
    print(f"Dashboard: http://{host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        server.server_close()
    return 0


_DASHBOARD_HTML = """<!doctype html><html lang="en"><meta charset="utf-8"><title>ByteWolf Telemetry</title>
<style>body{font:16px system-ui;margin:3rem;background:#101820;color:#edf2f4}main{max-width:720px}dl{display:grid;grid-template-columns:12rem 1fr;gap:.75rem;background:#1b2631;padding:1.5rem;border-radius:12px}dt{color:#a8dadc}dd{margin:0;font-variant-numeric:tabular-nums}.unknown{color:#f4a261}</style>
<main><h1>ByteWolf telemetry</h1><p>Local, read-only view. Refreshes every second.</p><dl><dt>Latitude</dt><dd id="lat">—</dd><dt>Longitude</dt><dd id="lon">—</dd><dt>Altitude</dt><dd id="alt">—</dd><dt>Battery</dt><dd id="battery">—</dd><dt>Flight state</dt><dd id="air">—</dd><dt>Captured</dt><dd id="captured">—</dd></dl><p id="status" class="unknown" aria-live="polite">Waiting for telemetry</p></main>
<script>const show=(id,value)=>document.getElementById(id).textContent=value??'Unavailable';const freshness=(t)=>{if(!t)return'MISSING: no capture timestamp';const time=Date.parse(t);if(Number.isNaN(time))return'INVALID: unreadable capture timestamp';const delta=Date.now()-time;if(delta<0)return'FUTURE: capture timestamp is ahead of this browser';return delta>10000?`STALE: ${Math.round(delta/1000)} seconds old`:'LIVE'};async function refresh(){try{const r=await fetch('/api/telemetry',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error);const p=d.position;show('lat',p?.latitude_deg?.toFixed(6));show('lon',p?.longitude_deg?.toFixed(6));show('alt',p?`${p.absolute_altitude_m.toFixed(1)} m`:null);show('battery',d.battery_percent==null?null:`${d.battery_percent.toFixed(1)} %`);show('air',d.in_air==null?null:d.in_air?'In air':'On ground');show('captured',d.captured_at);show('status',freshness(d.captured_at));}catch(e){show('status',`MISSING: waiting for valid telemetry (${e.message})`)}}refresh();setInterval(refresh,1000);</script></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
