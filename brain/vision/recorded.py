"""Recorded JSONL fixture ingest for the dependency-free Vision Core.

The adapter deliberately accepts only complete, hash-bound fixture records. It
does not decode images, call ML SDKs, or expose a control surface; image bytes
are retained solely so a caller can publish the verified recorded frame.
"""

from __future__ import annotations

import base64
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .contracts import BoundingBox, CameraFrame, Detection, VisionContractError
from .evaluation import GroundTruth, GroundTruthValidationError


class RecordedFixtureError(ValueError):
    """A recorded fixture is malformed and must not become a no-detection."""


@dataclass(frozen=True)
class _RecordedFrame:
    frame: CameraFrame
    payload: bytes
    detections: tuple[Detection, ...]
    ground_truth: tuple[GroundTruth, ...] | None


class RecordedJsonlIngest:
    """Read one hash-verified JSONL frame at a time for deterministic replay."""

    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            self._lines = tuple(line for line in path.read_text(encoding="utf-8").splitlines() if line.strip())
        except OSError as error:
            raise RecordedFixtureError(f"cannot read recorded fixture: {error}") from error
        self._position = 0
        self._records: dict[tuple[str, str, str, int, str], _RecordedFrame] = {}
        self._ground_truth_mode: bool | None = None

    @property
    def exhausted(self) -> bool:
        return self._position >= len(self._lines)

    @property
    def has_ground_truth(self) -> bool:
        return self._ground_truth_mode is True

    def poll(self) -> CameraFrame | None:
        if self.exhausted:
            return None
        line_number = self._position + 1
        line = self._lines[self._position]
        self._position += 1
        record = _parse_record(line, self._path, line_number)
        self._validate_ground_truth_mode(record)
        key = _frame_key(record.frame)
        self._records[key] = record
        return record.frame

    def payload_for(self, frame: CameraFrame) -> bytes:
        record = self._records.get(_frame_key(frame))
        if record is None:
            raise RecordedFixtureError("recorded payload was not available for this frame")
        return record.payload

    def detections_for(self, frame: CameraFrame) -> tuple[Detection, ...]:
        record = self._records.get(_frame_key(frame))
        if record is None:
            raise RecordedFixtureError("recorded annotations were not available for this frame")
        return record.detections

    def ground_truth_for(self, frame: CameraFrame) -> tuple[GroundTruth, ...]:
        record = self._records.get(_frame_key(frame))
        if record is None:
            raise RecordedFixtureError("recorded ground truth was not available for this frame")
        if record.ground_truth is None:
            raise RecordedFixtureError("recorded fixture did not declare ground truth for this frame")
        return record.ground_truth

    def _validate_ground_truth_mode(self, record: _RecordedFrame) -> None:
        has_ground_truth = record.ground_truth is not None
        if self._ground_truth_mode is None:
            self._ground_truth_mode = has_ground_truth
            return
        if self._ground_truth_mode != has_ground_truth:
            raise RecordedFixtureError("ground_truth presence must be consistent across the entire recorded fixture")


class AnnotatedFixtureDetector:
    """Deterministic detector that returns fixture annotations, never inference."""

    model_id = "recorded-annotation"
    model_version = "v1"

    def __init__(self, source: RecordedJsonlIngest) -> None:
        self._source = source

    def detect(self, frame: CameraFrame, produced_at: datetime) -> tuple[Detection, ...]:
        return self._source.detections_for(frame)


def _parse_record(line: str, path: Path, line_number: int) -> _RecordedFrame:
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as error:
        raise RecordedFixtureError(f"invalid JSON at {path}:{line_number}: {error.msg}") from error
    if not isinstance(raw, dict):
        raise RecordedFixtureError(f"record at {path}:{line_number} must be a JSON object")
    try:
        payload_text = raw["payload_base64"]
        if not isinstance(payload_text, str):
            raise TypeError("payload_base64 must be a string")
        payload = base64.b64decode(payload_text, validate=True)
        if not payload:
            raise ValueError("payload_base64 must decode to non-empty bytes")
        expected_hash = raw["payload_hash"]
        if not isinstance(expected_hash, str) or hashlib.sha256(payload).hexdigest() != expected_hash:
            raise ValueError("payload hash does not match payload_base64")
        frame = CameraFrame(
            contract_version=_required_str(raw, "contract_version"),
            device_id=_required_str(raw, "device_id"),
            camera_id=_required_str(raw, "camera_id"),
            stream_session_id=_required_str(raw, "stream_session_id"),
            frame_sequence=raw["frame_sequence"],
            captured_at=_timestamp(raw["captured_at"]),
            received_at=_timestamp(raw["received_at"]),
            calibration_version=_required_str(raw, "calibration_version"),
            payload_hash=expected_hash,
            encoding=_required_str(raw, "encoding"),
            width_px=raw["width_px"], height_px=raw["height_px"],
            latency_ms=raw["latency_ms"], dropped_frames=raw["dropped_frames"],
        )
        detections = _detections(raw.get("detections", []))
        ground_truth = _ground_truth(raw["ground_truth"]) if "ground_truth" in raw else None
    except (KeyError, TypeError, ValueError, VisionContractError, GroundTruthValidationError) as error:
        raise RecordedFixtureError(f"invalid record at {path}:{line_number}: {error}") from error
    return _RecordedFrame(frame, payload, detections, ground_truth)


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise TypeError("timestamp must be an RFC3339 string")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _required_str(raw: dict[str, Any], key: str) -> str:
    value = raw[key]
    if not isinstance(value, str):
        raise TypeError(f"{key} must be a string")
    return value


def _detections(value: object) -> tuple[Detection, ...]:
    if not isinstance(value, list):
        raise TypeError("detections must be a list")
    parsed: list[Detection] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("bounding_box"), dict):
            raise TypeError("each detection needs a bounding_box object")
        box = item["bounding_box"]
        parsed.append(Detection(
            label=item["label"], confidence=item["confidence"],
            bounding_box=BoundingBox(box["x_px"], box["y_px"], box["width_px"], box["height_px"]),
            tracker_id=item.get("tracker_id"),
        ))
    return tuple(parsed)


def _ground_truth(value: object) -> tuple[GroundTruth, ...]:
    if not isinstance(value, list):
        raise TypeError("ground_truth must be a list")
    parsed: list[GroundTruth] = []
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("bounding_box"), dict):
            raise TypeError("each ground_truth entry needs a bounding_box object")
        box = item["bounding_box"]
        parsed.append(GroundTruth(
            target_id=item["target_id"],
            label=item["label"],
            bounding_box=BoundingBox(box["x_px"], box["y_px"], box["width_px"], box["height_px"]),
        ))
    return tuple(parsed)


def _frame_key(frame: CameraFrame) -> tuple[str, str, str, int, str]:
    return (frame.device_id, frame.camera_id, frame.stream_session_id, frame.frame_sequence, frame.payload_hash)
