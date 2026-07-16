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
            self._reject_mutation()

        def do_PUT(self) -> None:  # noqa: N802 - intentional read-only boundary
            self._reject_mutation()

        def do_PATCH(self) -> None:  # noqa: N802 - intentional read-only boundary
            self._reject_mutation()

        def do_DELETE(self) -> None:  # noqa: N802 - intentional read-only boundary
            self._reject_mutation()

        def _reject_mutation(self) -> None:
            self._send(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "text/plain; charset=utf-8",
                b"Read-only dashboard\n",
                allow="GET",
            )

        def _send_telemetry(self) -> None:
            try:
                snapshot = load_telemetry_snapshot(telemetry_path)
                body = json.dumps(snapshot.as_dict()).encode()
                self._send(HTTPStatus.OK, "application/json", body)
            except TelemetryFormatError as error:
                self._send(HTTPStatus.BAD_REQUEST, "application/json", json.dumps({"error": str(error)}).encode())

        def _send(
            self,
            status: HTTPStatus,
            content_type: str,
            body: bytes,
            *,
            allow: str | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if allow is not None:
                self.send_header("Allow", allow)
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
<style>
:root{color-scheme:dark}body{font:16px system-ui;margin:0;background:#101820;color:#edf2f4}main{max-width:900px;margin:3rem auto;padding:0 1.25rem}.eyebrow{color:#a8dadc;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.status{display:flex;gap:.6rem;align-items:center;font-weight:700}.dot{width:.75rem;height:.75rem;border-radius:50%;background:#f4a261}.live .dot{background:#64d29b}.stale .dot{background:#f4a261}.missing .dot{background:#a8b5bd}.error .dot{background:#ef476f}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-top:1.5rem}.card{background:#1b2631;padding:1.25rem;border-radius:12px}.label{color:#a8dadc}.value{font-size:1.65rem;font-weight:700;margin-top:.25rem;font-variant-numeric:tabular-nums}.detail{margin-top:.4rem;color:#cdd8df;font-variant-numeric:tabular-nums}progress{width:100%;height:1rem;accent-color:#64d29b;margin-top:.75rem}</style>
<main><p class="eyebrow">Local, read-only simulation view</p><h1>ByteWolf telemetry</h1><p id="connection" class="status missing" aria-live="polite"><span class="dot"></span><span>Waiting for telemetry</span></p><section class="grid" aria-label="Live flight telemetry"><article class="card"><div class="label">Flight state</div><div id="flight-state" class="value">Unavailable</div><div id="captured" class="detail">No capture timestamp</div></article><article class="card"><div class="label">Relative altitude</div><div id="relative-altitude" class="value">Unavailable</div><div class="detail">Above takeoff point</div></article><article class="card"><div class="label">Battery</div><div id="battery" class="value">Unavailable</div><progress id="battery-meter" max="100" value="0">0%</progress></article><article class="card"><div class="label">Coordinates</div><div id="lat" class="detail">Latitude: Unavailable</div><div id="lon" class="detail">Longitude: Unavailable</div><div id="alt" class="detail">Absolute altitude: Unavailable</div></article></section></main>
<script>const show=(id,value)=>document.getElementById(id).textContent=value??'Unavailable';const freshness=(t)=>{if(!t)return['missing','MISSING: no capture timestamp'];const time=Date.parse(t);if(Number.isNaN(time))return['error','INVALID: unreadable capture timestamp'];const delta=Date.now()-time;if(delta<0)return['error','FUTURE: capture timestamp is ahead of this browser'];return delta>10000?['stale',`STALE: ${Math.round(delta/1000)} seconds old`]:['live','LIVE: telemetry connected']};const setConnection=([state,message])=>{const e=document.getElementById('connection');e.className=`status ${state}`;e.lastElementChild.textContent=message};async function refresh(){try{const r=await fetch('/api/telemetry',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error);const p=d.position;show('lat',p?`Latitude: ${p.latitude_deg.toFixed(6)}`:null);show('lon',p?`Longitude: ${p.longitude_deg.toFixed(6)}`:null);show('alt',p?`Absolute altitude: ${p.absolute_altitude_m.toFixed(1)} m`:null);show('relative-altitude',p?.relative_altitude_m==null?null:`${p.relative_altitude_m.toFixed(1)} m`);show('battery',d.battery_percent==null?null:`${d.battery_percent.toFixed(1)} %`);document.getElementById('battery-meter').value=d.battery_percent??0;show('flight-state',d.in_air==null?null:d.in_air?'IN AIR':'ON GROUND');show('captured',d.captured_at?`Captured: ${d.captured_at}`:null);setConnection(freshness(d.captured_at));}catch(e){setConnection(['error',`MISSING: waiting for valid telemetry (${e.message})`])}}refresh();setInterval(refresh,1000);</script></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
