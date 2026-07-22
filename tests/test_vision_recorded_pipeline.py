from __future__ import annotations

import base64
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import cv2
import numpy

from brain.cli.vision_recorded_pipeline import main, parse_arguments, run_recorded_pipeline
from brain.vision.recorded import RecordedFixtureError


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
_ENCODED, JPEG = cv2.imencode(".jpg", numpy.zeros((20, 20, 3), dtype=numpy.uint8))


def line(sequence: int, payload: bytes) -> str:
    return json.dumps({
        "contract_version": "camera_frame.v1", "device_id": "sim-01", "camera_id": "front-rgb", "stream_session_id": "run-1", "frame_sequence": sequence,
        "captured_at": "2026-07-21T11:59:59.995Z", "received_at": "2026-07-21T12:00:00Z", "calibration_version": "v1",
        "payload_hash": hashlib.sha256(payload).hexdigest(), "encoding": "jpeg", "width_px": 640, "height_px": 480, "latency_ms": 5.0, "dropped_frames": 0,
        "payload_base64": base64.b64encode(payload).decode("ascii"), "detections": [],
    })


def annotated_line(sequence: int, payload: bytes, *, include_ground_truth: bool) -> str:
    record = json.loads(line(sequence, payload))
    detection = {
        "label": "person",
        "confidence": 0.95,
        "bounding_box": {"x_px": 12, "y_px": 18, "width_px": 24, "height_px": 32},
    }
    record["detections"] = [detection]
    if include_ground_truth:
        record["ground_truth"] = [{
            "target_id": "subject-1",
            "label": "person",
            "bounding_box": {"x_px": 12, "y_px": 18, "width_px": 24, "height_px": 32},
        }]
    return json.dumps(record)


class RecordedPipelineTests(unittest.TestCase):
    def test_annotations_fixture_mode_publishes_frame_status_and_deterministic_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(line(1, bytes(JPEG)) + "\n" + line(2, bytes(JPEG)) + "\n")
            report = run_recorded_pipeline(
                fixture, root / "status.json", root / "frame.jpg", now=NOW,
                detector="annotations",
            )

            self.assertEqual(report["processed_frames"], 2)
            self.assertEqual(report["rejected_frames"], 0)
            self.assertEqual(report["benchmark"]["p50_latency_ms"], 5.0)
            self.assertIsNone(report["benchmark"]["precision"])
            self.assertEqual(report["benchmark"]["quality_kpis"], "unavailable_without_ground_truth")
            self.assertEqual(json.loads((root / "status.json").read_text())["state"], "valid")
            self.assertTrue((root / "frame.jpg").read_bytes().startswith(b"\xff\xd8"))

    def test_cli_writes_json_report(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(line(1, bytes(JPEG)) + "\n")
            report_path = root / "report.json"

            exit_code = main([
                str(fixture), "--detector", "annotations",
                "--status-path", str(root / "status.json"), "--frame-path", str(root / "frame.jpg"),
                "--report-path", str(report_path), "--now", "2026-07-21T12:00:00Z",
            ])

            self.assertEqual(exit_code, 0)
            self.assertEqual(json.loads(report_path.read_text())["processed_frames"], 1)

    def test_ground_truth_fixture_reports_quality_kpis(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(
                annotated_line(1, bytes(JPEG), include_ground_truth=True) + "\n"
                + annotated_line(2, bytes(JPEG), include_ground_truth=True) + "\n",
                encoding="utf-8",
            )

            report = run_recorded_pipeline(
                fixture, root / "status.json", root / "frame.jpg", now=NOW,
                detector="annotations",
            )

            benchmark = report["benchmark"]
            self.assertEqual(benchmark["quality_kpis"], "ground_truth_attached")
            self.assertEqual(benchmark["precision"], 1.0)
            self.assertEqual(benchmark["recall"], 1.0)
            self.assertEqual(benchmark["id_switches"], 0)
            self.assertEqual(benchmark["fragmentations"], 0)
            self.assertEqual(benchmark["reacquisitions"], 0)

    def test_partial_ground_truth_fixture_is_rejected_fail_closed(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(
                annotated_line(1, bytes(JPEG), include_ground_truth=True) + "\n"
                + annotated_line(2, bytes(JPEG), include_ground_truth=False) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(RecordedFixtureError, "ground_truth presence must be consistent"):
                run_recorded_pipeline(
                    fixture, root / "status.json", root / "frame.jpg", now=NOW,
                    detector="annotations",
                )

    def test_yolo11n_is_the_research_default_detector(self) -> None:
        args = parse_arguments(["fixture.jsonl", "--status-path", "status.json", "--frame-path", "frame.jpg"])

        self.assertEqual(args.detector, "yolo")
        self.assertIsNone(args.weights)

    def test_default_yolo_mode_requires_explicit_local_weights(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(line(1, bytes(JPEG)) + "\n")

            with self.assertRaisesRegex(SystemExit, "approved local weights file"):
                main([
                    str(fixture), "--status-path", str(root / "status.json"),
                    "--frame-path", str(root / "frame.jpg"),
                ])

    def test_yolo_requires_an_existing_local_weights_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(line(1, bytes(JPEG)) + "\n")

            with self.assertRaisesRegex(SystemExit, "approved local weights file"):
                main([
                    str(fixture), "--detector", "yolo", "--weights", str(root / "missing.pt"),
                    "--status-path", str(root / "status.json"), "--frame-path", str(root / "frame.jpg"),
                ])

    def test_yolo_uses_hash_verified_recorded_payload_resolver(self) -> None:
        class FakeYoloDetector:
            model_id = "research-yolo11n"
            model_version = "weights.pt"

            def __init__(self, model_id, model_version, resolver, *, weights_path):
                self.model_id = model_id
                self.model_version = model_version
                self._resolver = resolver

            def detect(self, frame, _produced_at):
                self._resolver.resolve(frame.payload_hash)
                return ()

        with TemporaryDirectory() as directory, patch("brain.cli.vision_recorded_pipeline.UltralyticsYoloDetector", FakeYoloDetector):
            root = Path(directory)
            fixture = root / "frames.jsonl"
            fixture.write_text(line(1, bytes(JPEG)) + "\n")
            weights = root / "approved.pt"
            weights.write_bytes(b"local-model-weights")

            report = run_recorded_pipeline(
                fixture, root / "status.json", root / "frame.jpg", now=NOW,
                detector="yolo", weights_path=weights,
            )

            self.assertEqual(report["processed_frames"], 1)
            self.assertEqual(report["detector"], "yolo")
            self.assertEqual(report["model_id"], "research-yolo11n")


if __name__ == "__main__":
    unittest.main()
