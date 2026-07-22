"""Run one locally provisioned YOLO model against one JPEG observation.

The command is intentionally a narrow P0 diagnostic: it exercises the same
hash-bound detector adapter as runtime ingestion, but has no stream, dashboard,
or control-plane dependency.  It never downloads model weights.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from typing import Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brain.vision.contracts import CAMERA_FRAME_V1, DETECTION_RESULT_V1, CameraFrame, DetectionResult
from brain.vision.ultralytics import UltralyticsYoloDetector


class _SinglePayloadResolver:
    """Resolve exactly one verified input payload for the detector adapter."""

    def __init__(self, payload_hash: str, payload: bytes) -> None:
        self._payload_hash = payload_hash
        self._payload = payload

    def resolve(self, payload_hash: str) -> bytes:
        if payload_hash != self._payload_hash:
            raise ValueError("YOLO smoke resolver rejected an unknown payload hash.")
        return self._payload


def _approved_file(path: Path, *, label: str) -> Path:
    if not path.is_file():
        raise ValueError(f"{label} must be an existing local file.")
    return path.resolve()


def _read_image_dimensions(payload: bytes) -> tuple[int, int]:
    try:
        import cv2
        import numpy
    except ImportError as error:  # pragma: no cover - deployment guard
        raise RuntimeError("OpenCV and NumPy are required for the YOLO smoke command.") from error
    image = cv2.imdecode(numpy.frombuffer(payload, dtype=numpy.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("image is not a decodable JPEG.")
    height_px, width_px = image.shape[:2]
    return int(width_px), int(height_px)


def _frame_document(frame: CameraFrame) -> dict[str, object]:
    """Expose immutable frame metadata only; JPEG bytes never leave this CLI."""
    return {
        "contract_version": frame.contract_version,
        "device_id": frame.device_id,
        "camera_id": frame.camera_id,
        "stream_session_id": frame.stream_session_id,
        "frame_sequence": frame.frame_sequence,
        "captured_at": frame.captured_at.isoformat(),
        "received_at": frame.received_at.isoformat(),
        "calibration_version": frame.calibration_version,
        "payload_hash": frame.payload_hash,
        "encoding": frame.encoding,
        "width_px": frame.width_px,
        "height_px": frame.height_px,
        "latency_ms": frame.latency_ms,
        "dropped_frames": frame.dropped_frames,
    }


def _detection_document(result: DetectionResult) -> list[dict[str, object]]:
    return [
        {
            "label": detection.label,
            "confidence": detection.confidence,
            "bounding_box": {
                "x_px": detection.bounding_box.x_px,
                "y_px": detection.bounding_box.y_px,
                "width_px": detection.bounding_box.width_px,
                "height_px": detection.bounding_box.height_px,
            },
            **({"tracker_id": detection.tracker_id} if detection.tracker_id is not None else {}),
        }
        for detection in result.detections
    ]


def run_yolo_smoke(image_path: Path, weights_path: Path, *, now: datetime) -> dict[str, object]:
    """Return one versioned, payload-free YOLO observation from local files."""
    image = _approved_file(image_path, label="image")
    if image.suffix.lower() not in {".jpg", ".jpeg"}:
        raise ValueError("image must have a JPEG (.jpg or .jpeg) filename.")
    weights = _approved_file(weights_path, label="approved local weights")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware.")
    payload = image.read_bytes()
    if not payload:
        raise ValueError("image cannot be empty.")
    width_px, height_px = _read_image_dimensions(payload)
    payload_hash = hashlib.sha256(payload).hexdigest()
    frame = CameraFrame(
        contract_version=CAMERA_FRAME_V1,
        device_id="local-smoke",
        camera_id="local-image",
        stream_session_id=f"smoke-{payload_hash[:16]}",
        frame_sequence=0,
        captured_at=now.astimezone(UTC),
        received_at=now.astimezone(UTC),
        calibration_version="local-image.v1",
        payload_hash=payload_hash,
        encoding="jpeg",
        width_px=width_px,
        height_px=height_px,
        latency_ms=0.0,
        dropped_frames=0,
    )
    detector = UltralyticsYoloDetector(
        "research-yolo11n", weights.name, _SinglePayloadResolver(payload_hash, payload), weights_path=str(weights),
    )
    result = DetectionResult(
        DETECTION_RESULT_V1, frame, detector.model_id, detector.model_version, now.astimezone(UTC), detector.detect(frame, now),
    )
    return {
        "contract_version": "vision_yolo_smoke.v1",
        "model_id": result.model_id,
        "model_version": result.model_version,
        "produced_at": result.produced_at.isoformat(),
        "frame": _frame_document(frame),
        "detections": _detection_document(result),
    }


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run locally provisioned YOLO weights against one JPEG without downloading models.")
    parser.add_argument("image_path", type=Path, help="Existing JPEG image to observe")
    parser.add_argument("--weights", type=Path, required=True, help="Existing approved local .pt weights file")
    parser.add_argument("--now", help="Optional RFC3339 timestamp for deterministic output")
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> int:
    args = parse_arguments(arguments)
    now = datetime.now(UTC) if args.now is None else datetime.fromisoformat(args.now.replace("Z", "+00:00"))
    try:
        print(json.dumps(run_yolo_smoke(args.image_path, args.weights, now=now), sort_keys=True, separators=(",", ":")))
    except (OSError, ValueError) as error:
        raise SystemExit(f"YOLO smoke input rejected: {error}") from error
    return 0


if __name__ == "__main__":
    main()
