from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from brain.vision.contracts import BoundingBox, Detection

from brain.cli.vision_yolo_smoke import main, run_yolo_smoke


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)


class YoloSmokeTests(unittest.TestCase):
    def test_direct_script_entrypoint_bootstraps_repo_imports(self) -> None:
        script_path = Path(__file__).resolve().parents[1] / "brain" / "cli" / "vision_yolo_smoke.py"
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)

        result = subprocess.run(
            ["/Users/vargaferenc/miniforge3/bin/python3", str(script_path), "--help"],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Run locally provisioned YOLO weights against one JPEG", result.stdout)

    def test_explicit_local_weights_and_image_produce_payload_free_observation_json(self) -> None:
        class FakeYoloDetector:
            def __init__(self, model_id, model_version, resolver, *, weights_path):
                self.model_id = model_id
                self.model_version = model_version
                self._resolver = resolver
                self.weights_path = weights_path

            def detect(self, frame, produced_at):
                self._resolver.resolve(frame.payload_hash)
                return (Detection("person", 0.9, BoundingBox(1, 2, 8, 9)),)

        with TemporaryDirectory() as directory, patch(
            "brain.cli.vision_yolo_smoke._read_image_dimensions", return_value=(20, 30),
        ), patch("brain.cli.vision_yolo_smoke.UltralyticsYoloDetector", FakeYoloDetector):
            root = Path(directory)
            image = root / "scene.jpg"
            payload = b"jpeg-test-payload"
            image.write_bytes(payload)
            weights = root / "approved.pt"
            weights.write_bytes(b"local-weights")

            report = run_yolo_smoke(image, weights, now=NOW)

            self.assertEqual(report["contract_version"], "vision_yolo_smoke.v1")
            self.assertEqual(report["model_id"], "research-yolo11n")
            self.assertEqual(report["model_version"], "approved.pt")
            self.assertEqual(report["frame"]["width_px"], 20)
            self.assertEqual(report["frame"]["height_px"], 30)
            self.assertEqual(report["detections"], [{
                "label": "person", "confidence": 0.9,
                "bounding_box": {"x_px": 1, "y_px": 2, "width_px": 8, "height_px": 9},
            }])
            serialized = json.dumps(report)
            self.assertNotIn(payload.decode(), serialized)
            self.assertNotIn("payload_base64", serialized)
            self.assertEqual(report["frame"]["payload_hash"], hashlib.sha256(payload).hexdigest())

    def test_rejects_missing_or_non_jpeg_inputs(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "scene.png"
            image.write_bytes(b"not-a-jpeg")
            weights = root / "approved.pt"
            weights.write_bytes(b"local-weights")

            with self.assertRaisesRegex(ValueError, "JPEG"):
                run_yolo_smoke(image, weights, now=NOW)
            with self.assertRaisesRegex(ValueError, "image"):
                run_yolo_smoke(root / "missing.jpg", weights, now=NOW)

    def test_cli_requires_explicit_existing_weights(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            image = root / "scene.jpg"
            image.write_bytes(b"not-decoded")

            with self.assertRaisesRegex(SystemExit, "approved local weights"):
                main([str(image), "--weights", str(root / "missing.pt")])


if __name__ == "__main__":
    unittest.main()
