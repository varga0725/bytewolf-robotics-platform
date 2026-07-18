"""Serve the dashboard locally; this module has no control endpoints."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path

from apps.dashboard.telemetry import TelemetryFormatError, load_telemetry_snapshot


def create_handler(
    telemetry_path: Path,
    *,
    camera_path: Path | None = None,
    detections_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required stdlib handler name
            if self.path == "/":
                self._send(HTTPStatus.OK, "text/html; charset=utf-8", _DASHBOARD_HTML.encode())
            elif self.path == "/api/telemetry":
                self._send_telemetry()
            elif self.path == "/api/camera":
                self._send_camera()
            elif self.path == "/api/detections":
                self._send_detections()
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

        def _send_camera(self) -> None:
            # An unconfigured or absent frame is a missing view, not an empty one:
            # the endpoint stays 404 so a consumer cannot read stale bytes as live.
            if camera_path is None or not camera_path.is_file():
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"No camera frame\n")
                return
            self._send(HTTPStatus.OK, "image/jpeg", camera_path.read_bytes())

        def _send_detections(self) -> None:
            if detections_path is None or not detections_path.is_file():
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"No detections\n")
                return
            try:
                # Parse before serving so a malformed file fails closed rather than
                # reaching the overlay as broken JSON.
                body = json.dumps(json.loads(detections_path.read_text(encoding="utf-8"))).encode()
            except (OSError, json.JSONDecodeError) as error:
                self._send(HTTPStatus.BAD_REQUEST, "application/json", json.dumps({"error": str(error)}).encode())
                return
            self._send(HTTPStatus.OK, "application/json", body)

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
    parser.add_argument("--camera-file", type=Path, default=None, help="Optional read-only camera JPEG frame.")
    parser.add_argument("--detections-file", type=Path, default=None, help="Optional read-only detections JSON.")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    host = "127.0.0.1"
    server = ThreadingHTTPServer(
        (host, args.port),
        create_handler(args.telemetry_file, camera_path=args.camera_file, detections_path=args.detections_file),
    )
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
:root{color-scheme:dark}body{font:16px system-ui;margin:0;background:#101820;color:#edf2f4}main{max-width:900px;margin:3rem auto;padding:0 1.25rem}.eyebrow{color:#a8dadc;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.status{display:flex;gap:.6rem;align-items:center;font-weight:700}.dot{width:.75rem;height:.75rem;border-radius:50%;background:#f4a261}.live .dot{background:#64d29b}.stale .dot{background:#f4a261}.missing .dot{background:#a8b5bd}.error .dot{background:#ef476f}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-top:1.5rem}.card{background:#1b2631;padding:1.25rem;border-radius:12px}.label{color:#a8dadc}.value{font-size:1.65rem;font-weight:700;margin-top:.25rem;font-variant-numeric:tabular-nums}.detail{margin-top:.4rem;color:#cdd8df;font-variant-numeric:tabular-nums}progress{width:100%;height:1rem;accent-color:#64d29b;margin-top:.75rem}.camera{margin-top:1.5rem;background:#1b2631;padding:1.25rem;border-radius:12px}.frame{position:relative;margin-top:.75rem;line-height:0}.frame img{width:100%;border-radius:8px;display:block}.frame svg{position:absolute;inset:0;width:100%;height:100%}.frame rect{fill:none;stroke:#64d29b;stroke-width:2}.frame text{fill:#64d29b;font:12px system-ui}</style>
<main><p class="eyebrow">Local, read-only simulation view</p><h1>ByteWolf telemetry</h1><p id="connection" class="status missing" aria-live="polite"><span class="dot"></span><span>Waiting for telemetry</span></p><section class="grid" aria-label="Live flight telemetry"><article class="card"><div class="label">Flight state</div><div id="flight-state" class="value">Unavailable</div><div id="captured" class="detail">No capture timestamp</div></article><article class="card"><div class="label">Relative altitude</div><div id="relative-altitude" class="value">Unavailable</div><div class="detail">Above takeoff point</div></article><article class="card"><div class="label">Battery</div><div id="battery" class="value">Unavailable</div><progress id="battery-meter" max="100" value="0">0%</progress></article><article class="card"><div class="label">Coordinates</div><div id="lat" class="detail">Latitude: Unavailable</div><div id="lon" class="detail">Longitude: Unavailable</div><div id="alt" class="detail">Absolute altitude: Unavailable</div></article></section><section class="camera" aria-label="Read-only camera view"><div class="label">Front camera</div><div id="camera-status" class="detail">No camera frame</div><figure class="frame"><img id="camera-frame" alt="Front camera frame" hidden><svg id="detection-overlay" viewBox="0 0 640 480" preserveAspectRatio="none" aria-hidden="true"></svg></figure></section></main>
<script>const show=(id,value)=>document.getElementById(id).textContent=value??'Unavailable';const freshness=(t)=>{if(!t)return['missing','MISSING: no capture timestamp'];const time=Date.parse(t);if(Number.isNaN(time))return['error','INVALID: unreadable capture timestamp'];const delta=Date.now()-time;if(delta<0)return['error','FUTURE: capture timestamp is ahead of this browser'];return delta>10000?['stale',`STALE: ${Math.round(delta/1000)} seconds old`]:['live','LIVE: telemetry connected']};const setConnection=([state,message])=>{const e=document.getElementById('connection');e.className=`status ${state}`;e.lastElementChild.textContent=message};async function refresh(){try{const r=await fetch('/api/telemetry',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error);const p=d.position;show('lat',p?`Latitude: ${p.latitude_deg.toFixed(6)}`:null);show('lon',p?`Longitude: ${p.longitude_deg.toFixed(6)}`:null);show('alt',p?`Absolute altitude: ${p.absolute_altitude_m.toFixed(1)} m`:null);show('relative-altitude',p?.relative_altitude_m==null?null:`${p.relative_altitude_m.toFixed(1)} m`);show('battery',d.battery_percent==null?null:`${d.battery_percent.toFixed(1)} %`);document.getElementById('battery-meter').value=d.battery_percent??0;show('flight-state',d.in_air==null?null:d.in_air?'IN AIR':'ON GROUND');show('captured',d.captured_at?`Captured: ${d.captured_at}`:null);setConnection(freshness(d.captured_at));}catch(e){setConnection(['error',`MISSING: waiting for valid telemetry (${e.message})`])}}async function refreshCamera(){const img=document.getElementById('camera-frame');const svg=document.getElementById('detection-overlay');const status=document.getElementById('camera-status');try{const r=await fetch('/api/camera',{cache:'no-store'});if(!r.ok)throw Error('no frame');const blob=await r.blob();img.src=URL.createObjectURL(blob);img.hidden=false;status.textContent='Live camera frame';}catch(e){img.hidden=true;status.textContent='No camera frame';svg.replaceChildren();return}try{const r=await fetch('/api/detections',{cache:'no-store'});if(!r.ok)throw Error('no detections');const d=await r.json();const f=d.frame||{width:640,height:480};svg.setAttribute('viewBox',`0 0 ${f.width} ${f.height}`);const ns='http://www.w3.org/2000/svg';svg.replaceChildren(...(d.detections||[]).flatMap(det=>{const b=det.bbox;const rect=document.createElementNS(ns,'rect');rect.setAttribute('x',b.x);rect.setAttribute('y',b.y);rect.setAttribute('width',b.width);rect.setAttribute('height',b.height);const t=document.createElementNS(ns,'text');t.setAttribute('x',b.x);t.setAttribute('y',Math.max(12,b.y-4));t.textContent=`${det.label} ${(det.confidence*100).toFixed(0)}%`;return[rect,t]}));}catch(e){svg.replaceChildren()}}refresh();refreshCamera();setInterval(refresh,1000);setInterval(refreshCamera,1000);</script></html>"""


if __name__ == "__main__":
    raise SystemExit(main())
