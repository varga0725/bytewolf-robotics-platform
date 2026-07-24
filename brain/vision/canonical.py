"""Load and validate the shared Vision v0.1 contracts, failing closed.

Three versioned documents the Vision stack publishes and a consumer reads:

* ``DetectionEvent``  - one detection in one frame.
* ``VisionSummary``   - a frame's roll-up of detections and tracks.
* ``VisionHealth``    - a camera/vision stream's health snapshot.

Each loader validates against the frozen JSON Schema (with a format checker),
enforces the cross-field invariant JSON Schema cannot express (a bounding box
must lie within its frame), and refuses anything it cannot fully trust rather
than returning a best-effort value. Freshness is resolved here, not declared by
the producer: only the consumer knows the current time, so a detection older
than its ``max_age_s`` resolves to ``stale`` even though its values are present.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any

import jsonschema


VISION_CONTRACT_VERSION = "v0.1"

_SCHEMA_DIR = Path(__file__).resolve().parents[2] / "shared/schemas/vision"
DETECTION_EVENT_SCHEMA_PATH = _SCHEMA_DIR / "detection_event_v0_1.schema.json"
VISION_SUMMARY_SCHEMA_PATH = _SCHEMA_DIR / "vision_summary_v0_1.schema.json"
VISION_HEALTH_SCHEMA_PATH = _SCHEMA_DIR / "vision_health_v0_1.schema.json"


class VisionContractError(ValueError):
    """Raised when a document cannot be read as a Vision contract."""


class VisionState(Enum):
    """Whether a Vision observation may be acted on, and if not, why not."""

    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    STALE = "stale"

    @property
    def usable(self) -> bool:
        return self is VisionState.VALID


@dataclass(frozen=True)
class DetectionEvent:
    event_id: str
    source: str
    observed_at: datetime
    max_age_s: float
    declared_validity: str
    frame: dict[str, Any]
    model_id: str
    model_version: str
    label: str
    confidence: float
    bounding_box: dict[str, Any]
    artifact_ref: dict[str, Any] | None

    def state(self, now: datetime) -> VisionState:
        return _resolve_state(self.declared_validity, self.observed_at, self.max_age_s, now)


@dataclass(frozen=True)
class VisionSummary:
    source: str
    observed_at: datetime
    max_age_s: float
    declared_validity: str
    frame: dict[str, Any]
    model_id: str
    model_version: str
    detections: tuple[dict[str, Any], ...]
    detection_count: int

    def state(self, now: datetime) -> VisionState:
        return _resolve_state(self.declared_validity, self.observed_at, self.max_age_s, now)


@dataclass(frozen=True)
class VisionHealth:
    source: str
    observed_at: datetime
    max_age_s: float
    declared_validity: str
    stream_state: str
    fps: float | None
    backlog_frames: int | None
    dropped_frames: int | None
    model_id: str | None
    model_version: str | None
    detail: str | None

    def state(self, now: datetime) -> VisionState:
        return _resolve_state(self.declared_validity, self.observed_at, self.max_age_s, now)


@lru_cache(maxsize=None)
def _validator(schema_path: Path) -> Any:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise VisionContractError(f"Cannot read the vision schema '{schema_path}': {error.strerror}.") from error
    validator_class = jsonschema.validators.validator_for(schema)
    validator_class.check_schema(schema)
    return validator_class(schema, format_checker=validator_class.FORMAT_CHECKER)


def _validate(document: object, schema_path: Path, label: str) -> dict[str, Any]:
    try:
        _validator(schema_path).validate(document)
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise VisionContractError(f"{label} rejected at '{location}': {error.message}") from error
    assert isinstance(document, dict), "The schema requires an object at the root."
    return document


def _require_box_in_frame(frame: dict[str, Any], box: dict[str, Any]) -> None:
    if box["x_px"] + box["width_px"] > frame["width_px"] or box["y_px"] + box["height_px"] > frame["height_px"]:
        raise VisionContractError("bounding_box must lie within the frame.")


def load_detection_event(document: object) -> DetectionEvent:
    """Read a document as a DetectionEvent, or refuse it."""
    validated = _validate(document, DETECTION_EVENT_SCHEMA_PATH, "DetectionEvent")
    _require_box_in_frame(validated["frame"], validated["bounding_box"])
    return DetectionEvent(
        event_id=validated["event_id"],
        source=validated["source"],
        observed_at=_parse_timestamp(validated["observed_at"]),
        max_age_s=float(validated["max_age_s"]),
        declared_validity=validated["validity"],
        frame=validated["frame"],
        model_id=validated["model_id"],
        model_version=validated["model_version"],
        label=validated["label"],
        confidence=float(validated["confidence"]),
        bounding_box=validated["bounding_box"],
        artifact_ref=validated.get("artifact_ref"),
    )


def load_vision_summary(document: object) -> VisionSummary:
    """Read a document as a VisionSummary, or refuse it."""
    validated = _validate(document, VISION_SUMMARY_SCHEMA_PATH, "VisionSummary")
    frame = validated["frame"]
    for detection in validated["detections"]:
        _require_box_in_frame(frame, detection["bounding_box"])
    return VisionSummary(
        source=validated["source"],
        observed_at=_parse_timestamp(validated["observed_at"]),
        max_age_s=float(validated["max_age_s"]),
        declared_validity=validated["validity"],
        frame=frame,
        model_id=validated["model_id"],
        model_version=validated["model_version"],
        detections=tuple(validated["detections"]),
        detection_count=int(validated["detection_count"]),
    )


def load_vision_health(document: object) -> VisionHealth:
    """Read a document as a VisionHealth snapshot, or refuse it."""
    validated = _validate(document, VISION_HEALTH_SCHEMA_PATH, "VisionHealth")
    return VisionHealth(
        source=validated["source"],
        observed_at=_parse_timestamp(validated["observed_at"]),
        max_age_s=float(validated["max_age_s"]),
        declared_validity=validated["validity"],
        stream_state=validated["state"],
        fps=validated.get("fps"),
        backlog_frames=validated.get("backlog_frames"),
        dropped_frames=validated.get("dropped_frames"),
        model_id=validated.get("model_id"),
        model_version=validated.get("model_version"),
        detail=validated.get("detail"),
    )


def _resolve_state(validity: str, observed_at: datetime, max_age_s: float, now: datetime) -> VisionState:
    if validity == "missing":
        return VisionState.MISSING
    if validity == "invalid":
        return VisionState.INVALID
    age = max(0.0, (_utc(now) - observed_at).total_seconds())
    if age > max_age_s:
        return VisionState.STALE
    return VisionState.VALID


def _parse_timestamp(value: str) -> datetime:
    if "T" not in value and "t" not in value:
        raise VisionContractError(f"Vision timestamp '{value}' is not RFC 3339: date and time must be joined by 'T'.")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise VisionContractError(f"Vision timestamp '{value}' is not RFC 3339.") from error
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise VisionContractError(f"Vision timestamp '{value}' has no offset; an age cannot be measured from it.")
    return timestamp.astimezone(UTC)


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise VisionContractError("The current time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
