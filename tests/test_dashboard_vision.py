from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from apps.dashboard.server import create_handler


class DashboardVisionTests(unittest.TestCase):
    def test_exposes_optional_read_only_vision_status_and_frame(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            status = root / "vision-status.json"
            frame = root / "frame.jpg"
            telemetry.write_text("{}", encoding="utf-8")
            document = self._status_document()
            status.write_text(json.dumps(document), encoding="utf-8")
            frame.write_bytes(b"frame-bytes")
            server, thread, base_url = self._start(telemetry, status, frame)
            try:
                with urlopen(f"{base_url}/api/vision") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(json.loads(response.read()), document)
                with urlopen(f"{base_url}/api/vision/frame") as response:
                    self.assertEqual(response.status, 200)
                    self.assertEqual(response.headers["Content-Type"], "image/jpeg")
                    self.assertEqual(response.read(), b"frame-bytes")
            finally:
                self._stop(server, thread)

    def test_vision_endpoints_are_absent_without_configured_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            telemetry = Path(directory) / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")
            server, thread, base_url = self._start(telemetry)
            try:
                for endpoint in ("/api/vision", "/api/vision/frame"):
                    with self.subTest(endpoint=endpoint), self.assertRaises(HTTPError) as error:
                        urlopen(f"{base_url}{endpoint}")
                    self.assertEqual(error.exception.code, 404)
            finally:
                self._stop(server, thread)

    def test_dashboard_renders_a_read_only_vision_panel(self) -> None:
        with TemporaryDirectory() as directory:
            telemetry = Path(directory) / "telemetry.json"
            telemetry.write_text("{}", encoding="utf-8")
            server, thread, base_url = self._start(telemetry)
            try:
                with urlopen(base_url) as response:
                    body = response.read().decode("utf-8")
                self.assertIn('id="vision-state"', body)
                self.assertIn('id="vision-frame"', body)
                self.assertIn("fetch('/api/vision'", body)
                self.assertIn("/api/vision/frame", body)
            finally:
                self._stop(server, thread)

    def test_rejects_malformed_vision_status_without_turning_it_into_a_command_surface(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            status = root / "vision-status.json"
            telemetry.write_text("{}", encoding="utf-8")
            status.write_text("[]", encoding="utf-8")
            server, thread, base_url = self._start(telemetry, status)
            try:
                with self.assertRaises(HTTPError) as error:
                    urlopen(f"{base_url}/api/vision")
                self.assertEqual(error.exception.code, 400)
                self.assertEqual(json.loads(error.exception.read()), {"error": "Vision status must be a JSON object."})
            finally:
                self._stop(server, thread)

    def test_rejects_sensitive_or_control_fields_in_vision_artifacts(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            status = root / "vision-status.json"
            telemetry.write_text("{}", encoding="utf-8")
            for field in ("embedding", "template", "payload", "evidence_path", "command", "mission"):
                with self.subTest(field=field):
                    document = self._status_document()
                    document[field] = "must not escape"
                    status.write_text(json.dumps(document), encoding="utf-8")
                    server, thread, base_url = self._start(telemetry, status)
                    try:
                        with self.assertRaises(HTTPError) as error:
                            urlopen(f"{base_url}/api/vision")
                        self.assertEqual(error.exception.code, 400)
                    finally:
                        self._stop(server, thread)

    def test_rejects_non_finite_or_out_of_range_detection_confidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            telemetry = root / "telemetry.json"
            status = root / "vision-status.json"
            telemetry.write_text("{}", encoding="utf-8")
            for confidence in ("NaN", -0.1, 1.1):
                with self.subTest(confidence=confidence):
                    document = self._status_document()
                    document["detections"] = [{
                        "label": "person", "confidence": confidence, "tracker_id": "local-000001",
                        "bounding_box": {"x_px": 0, "y_px": 0, "width_px": 1, "height_px": 1},
                    }]
                    status.write_text(json.dumps(document), encoding="utf-8")
                    server, thread, base_url = self._start(telemetry, status)
                    try:
                        with self.assertRaises(HTTPError) as error:
                            urlopen(f"{base_url}/api/vision")
                        self.assertEqual(error.exception.code, 400)
                    finally:
                        self._stop(server, thread)

    @staticmethod
    def _status_document() -> dict[str, object]:
        return {
            "contract_version": "vision_dashboard.v1", "state": "valid", "observed_at": "2026-07-21T12:00:00Z",
            "track_count": 1, "detections": [], "backlog_frames": 0, "dropped_frames": 0,
            "stream_state": "healthy", "model_state": "healthy", "gpu_state": "healthy",
        }

    def _start(
        self, telemetry: Path, status: Path | None = None, frame: Path | None = None
    ) -> tuple[ThreadingHTTPServer, Thread, str]:
        server = ThreadingHTTPServer(("127.0.0.1", 0), create_handler(telemetry, status, frame))
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, f"http://127.0.0.1:{server.server_port}"

    def _stop(self, server: ThreadingHTTPServer, thread: Thread) -> None:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    unittest.main()
