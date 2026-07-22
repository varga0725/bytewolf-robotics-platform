"""Serve the dashboard locally; this module has no control endpoints."""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from math import isfinite
from pathlib import Path

from apps.dashboard.telemetry import TelemetryFormatError, load_telemetry_snapshot


def create_handler(
    telemetry_path: Path,
    vision_status_path: Path | None = None,
    vision_frame_path: Path | None = None,
) -> type[BaseHTTPRequestHandler]:
    """Create a read-only dashboard handler with optional Vision artifacts.

    Vision produces local immutable artifacts.  The dashboard only reads them;
    accepting a path here must never turn the dashboard into an ingest or
    control endpoint.
    """
    class DashboardHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - required stdlib handler name
            path = self.path.split("?", 1)[0]
            if path == "/":
                self._send(
                    HTTPStatus.OK,
                    "text/html; charset=utf-8",
                    _DASHBOARD_HTML.replace("</main>", _VISION_PANEL + "</main>").encode(),
                )
            elif path == "/api/telemetry":
                self._send_telemetry()
            elif path == "/api/vision":
                self._send_vision_status()
            elif path == "/api/vision/frame":
                self._send_vision_frame()
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

        def _send_vision_status(self) -> None:
            if vision_status_path is None:
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Vision is not configured\n")
                return
            try:
                document = json.loads(vision_status_path.read_text(encoding="utf-8"))
            except OSError:
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Vision status is unavailable\n")
                return
            except json.JSONDecodeError:
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    "application/json",
                    json.dumps({"error": "Vision status must contain valid JSON."}).encode(),
                )
                return
            try:
                read_model = _vision_read_model(document)
            except ValueError as error:
                self._send(
                    HTTPStatus.BAD_REQUEST,
                    "application/json",
                    json.dumps({"error": str(error)}).encode(),
                )
                return
            self._send(HTTPStatus.OK, "application/json", json.dumps(read_model).encode())

        def _send_vision_frame(self) -> None:
            if vision_frame_path is None or not vision_frame_path.is_file():
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Vision frame is unavailable\n")
                return
            try:
                body = vision_frame_path.read_bytes()
            except OSError:
                self._send(HTTPStatus.NOT_FOUND, "text/plain; charset=utf-8", b"Vision frame is unavailable\n")
                return
            self._send(HTTPStatus.OK, _image_content_type(vision_frame_path), body)

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
    parser.add_argument("--vision-status-file", type=Path)
    parser.add_argument("--vision-frame-file", type=Path)
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args(argv)
    host = "127.0.0.1"
    server = ThreadingHTTPServer(
        (host, args.port),
        create_handler(args.telemetry_file, args.vision_status_file, args.vision_frame_file),
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
:root{color-scheme:dark}body{font:16px system-ui;margin:0;background:#101820;color:#edf2f4}main{max-width:900px;margin:3rem auto;padding:0 1.25rem}.eyebrow{color:#a8dadc;font-weight:700;text-transform:uppercase;letter-spacing:.08em}.status{display:flex;gap:.6rem;align-items:center;font-weight:700}.dot{width:.75rem;height:.75rem;border-radius:50%;background:#f4a261}.live .dot{background:#64d29b}.stale .dot{background:#f4a261}.missing .dot{background:#a8b5bd}.error .dot{background:#ef476f}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:1rem;margin-top:1.5rem}.card{background:#1b2631;padding:1.25rem;border-radius:12px}.label{color:#a8dadc}.value{font-size:1.65rem;font-weight:700;margin-top:.25rem;font-variant-numeric:tabular-nums}.detail{margin-top:.4rem;color:#cdd8df;font-variant-numeric:tabular-nums}progress{width:100%;height:1rem;accent-color:#64d29b;margin-top:.75rem}</style>
<main><p class="eyebrow">Local, read-only simulation view</p><h1>ByteWolf telemetry</h1><p id="connection" class="status missing" aria-live="polite"><span class="dot"></span><span>Waiting for telemetry</span></p><section class="grid" aria-label="Live flight telemetry"><article class="card"><div class="label">Flight state</div><div id="flight-state" class="value">Unavailable</div><div id="captured" class="detail">No capture timestamp</div></article><article class="card"><div class="label">Relative altitude</div><div id="relative-altitude" class="value">Unavailable</div><div class="detail">Above takeoff point</div></article><article class="card"><div class="label">Battery</div><div id="battery" class="value">Unavailable</div><progress id="battery-meter" max="100" value="0">0%</progress></article><article class="card"><div class="label">Coordinates</div><div id="lat" class="detail">Latitude: Unavailable</div><div id="lon" class="detail">Longitude: Unavailable</div><div id="alt" class="detail">Absolute altitude: Unavailable</div></article></section></main>
<script>const show=(id,value)=>document.getElementById(id).textContent=value??'Unavailable';const freshness=(t)=>{if(!t)return['missing','MISSING: no capture timestamp'];const time=Date.parse(t);if(Number.isNaN(time))return['error','INVALID: unreadable capture timestamp'];const delta=Date.now()-time;if(delta<0)return['error','FUTURE: capture timestamp is ahead of this browser'];return delta>10000?['stale',`STALE: ${Math.round(delta/1000)} seconds old`]:['live','LIVE: telemetry connected']};const setConnection=([state,message])=>{const e=document.getElementById('connection');e.className=`status ${state}`;e.lastElementChild.textContent=message};async function refresh(){try{const r=await fetch('/api/telemetry',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error(d.error);const p=d.position;show('lat',p?`Latitude: ${p.latitude_deg.toFixed(6)}`:null);show('lon',p?`Longitude: ${p.longitude_deg.toFixed(6)}`:null);show('alt',p?`Absolute altitude: ${p.absolute_altitude_m.toFixed(1)} m`:null);show('relative-altitude',p?.relative_altitude_m==null?null:`${p.relative_altitude_m.toFixed(1)} m`);show('battery',d.battery_percent==null?null:`${d.battery_percent.toFixed(1)} %`);document.getElementById('battery-meter').value=d.battery_percent??0;show('flight-state',d.in_air==null?null:d.in_air?'IN AIR':'ON GROUND');show('captured',d.captured_at?`Captured: ${d.captured_at}`:null);setConnection(freshness(d.captured_at));}catch(e){setConnection(['error',`MISSING: waiting for valid telemetry (${e.message})`])}}refresh();setInterval(refresh,1000);</script></html>"""


_VISION_PANEL = """<section class="grid" aria-label="Read-only vision view"><article class="card"><div class="label">Vision state</div><div id="vision-state" class="value">Unavailable</div><div id="vision-detail" class="detail">No Vision artifact</div></article><article class="card"><div class="label">Camera overlay</div><img id="vision-frame" alt="Latest Vision overlay" style="display:none;max-width:100%;margin-top:.75rem"><div id="vision-tracks" class="detail">No tracks</div></article></section><script>async function refreshVision(){try{const r=await fetch('/api/vision',{cache:'no-store'});const d=await r.json();if(!r.ok)throw Error('unavailable');show('vision-state',String(d.state??'invalid').toUpperCase());show('vision-detail',`stream: ${d.stream_state??'missing'} · model: ${d.model_state??'missing'} · backlog: ${d.backlog_frames??0} · dropped: ${d.dropped_frames??0}`);show('vision-tracks',`Tracks: ${d.track_count??0}`);const f=document.getElementById('vision-frame');f.src='/api/vision/frame?cache='+Date.now();f.style.display='block'}catch(_){show('vision-state','UNAVAILABLE');show('vision-detail','No valid Vision status');show('vision-tracks','No tracks');document.getElementById('vision-frame').style.display='none'}}refreshVision();setInterval(refreshVision,1000);</script>"""


def _image_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return "application/octet-stream"


_VISION_READ_MODEL_FIELDS = frozenset(
    {
        "contract_version", "state", "observed_at", "track_count", "detections",
        "backlog_frames", "dropped_frames", "stream_state", "model_state", "gpu_state",
    }
)


def _vision_read_model(document: object) -> dict[str, object]:
    """Allowlist the dashboard's observation-only Vision read model.

    The local artifact directory is still a producer boundary: never relay raw
    payloads, embeddings, templates, evidence locations, or future command
    fields merely because they happen to be JSON.
    """
    if not isinstance(document, dict):
        raise ValueError("Vision status must be a JSON object.")
    unknown = set(document) - _VISION_READ_MODEL_FIELDS
    if unknown:
        raise ValueError("Vision status contains fields outside the read-only contract.")
    if document.get("contract_version") != "vision_dashboard.v1":
        raise ValueError("Vision status must declare contract_version vision_dashboard.v1.")
    if document.get("state") not in {"valid", "missing", "stale", "invalid"}:
        raise ValueError("Vision status has an invalid state.")
    detections = document.get("detections")
    if not isinstance(detections, list) or not all(_is_dashboard_detection(item) for item in detections):
        raise ValueError("Vision status detections do not match the read-only contract.")
    return {field: document.get(field) for field in _VISION_READ_MODEL_FIELDS if field in document}


def _is_dashboard_detection(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    allowed = {"label", "confidence", "tracker_id", "bounding_box"}
    box = value.get("bounding_box")
    confidence = value.get("confidence")
    return (
        set(value) <= allowed
        and isinstance(value.get("label"), str)
        and type(confidence) in (int, float)
        and isfinite(confidence)
        and 0.0 <= confidence <= 1.0
        and (value.get("tracker_id") is None or isinstance(value.get("tracker_id"), str))
        and isinstance(box, dict)
        and set(box) == {"x_px", "y_px", "width_px", "height_px"}
        and type(box["x_px"]) is int and box["x_px"] >= 0
        and type(box["y_px"]) is int and box["y_px"] >= 0
        and type(box["width_px"]) is int and box["width_px"] > 0
        and type(box["height_px"]) is int and box["height_px"] > 0
    )


if __name__ == "__main__":
    raise SystemExit(main())
