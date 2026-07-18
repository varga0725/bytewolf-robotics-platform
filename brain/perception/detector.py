"""Run a replaceable detector over a camera frame and report what it found.

This is the vision half of the perception path. Like the lidar adapter, it emits
data and never a command: it turns a camera frame into a versioned, validated
detection result, and the safety layer decides what, if anything, to do with it.

The detector backend is deliberately behind an interface. A deterministic stub
ships here so the whole path is testable without model weights, and a real
YOLO-compatible backend can replace it without touching MissionSpec, the safety
kernel, or this adapter's contract.

Every result is one of four states a consumer must tell apart, and only one may
be acted on:

* ``VALID``   - the detector ran on a fresh frame and trusts the result.
* ``INVALID`` - the detector ran but the result cannot be trusted: an unreadable
  frame, or a backend that raised.
* ``MISSING`` - no frame was available, so there is no result.
* ``STALE``   - trustworthy when captured, but older than its own max_age_s.

A detector failure becomes an explicit invalid result rather than an exception
that a caller might treat as "nothing detected", and staleness is derived from
the capture time so a slow pipeline cannot pass off an old frame as current.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any, Protocol

import jsonschema

from brain.perception.camera_frame import CameraFrame


DETECTION_CONTRACT_VERSION = "v0.1"
DETECTION_SCHEMA_PATH = (
    Path(__file__).resolve().parents[2] / "shared/schemas/perception/detection_v0_1.schema.json"
)
DEFAULT_MAX_AGE_S = 0.5


class DetectionContractError(ValueError):
    """Raised when a detection result cannot be read as the contract requires."""


@dataclass(frozen=True)
class BoundingBox:
    """A pixel box in the frame, origin top-left."""

    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Detection:
    """One detected object; a proposal for perception, never a command."""

    label: str
    confidence: float
    bbox: BoundingBox


class DetectorState(Enum):
    """Whether a detection result may be acted on, and if not, why not."""

    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    STALE = "stale"

    @property
    def usable(self) -> bool:
        return self is DetectorState.VALID


@dataclass(frozen=True)
class DetectionResult:
    """A schema-valid detector output and the detections it carries, if any."""

    captured_at: datetime
    max_age_s: float
    declared_validity: str
    frame_width: int
    frame_height: int
    frame_id: str | None
    detections: tuple[Detection, ...]
    source: str | None

    def age_s(self, now: datetime) -> float:
        return max(0.0, (_utc(now) - self.captured_at).total_seconds())

    def state(self, now: datetime) -> DetectorState:
        if self.declared_validity == "missing":
            return DetectorState.MISSING
        if self.declared_validity == "invalid":
            return DetectorState.INVALID
        if self.age_s(now) > self.max_age_s:
            return DetectorState.STALE
        return DetectorState.VALID

    def usable_detections(self, now: datetime) -> tuple[Detection, ...]:
        """Return detections only if the result may be acted on, else refuse."""
        state = self.state(now)
        if not state.usable:
            raise DetectionContractError(f"Detection result is {state.value} and must not be acted on.")
        return self.detections

    def to_document(self) -> dict[str, Any]:
        """The stable JSON shape, also what the read-only dashboard consumes."""
        frame: dict[str, Any] = {"width": self.frame_width, "height": self.frame_height}
        if self.frame_id is not None:
            frame["frame_id"] = self.frame_id
        document: dict[str, Any] = {
            "contract_version": DETECTION_CONTRACT_VERSION,
            "captured_at": self.captured_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
            "max_age_s": self.max_age_s,
            "validity": self.declared_validity,
            "frame": frame,
            "detections": [
                {
                    "label": detection.label,
                    "confidence": detection.confidence,
                    "bbox": {
                        "x": detection.bbox.x,
                        "y": detection.bbox.y,
                        "width": detection.bbox.width,
                        "height": detection.bbox.height,
                    },
                }
                for detection in self.detections
            ],
        }
        if self.source is not None:
            document["source"] = self.source
        return document


class DetectorBackend(Protocol):
    """A replaceable detector: a frame in, detections out. Never a command."""

    def detect(self, frame: CameraFrame) -> Sequence[Detection]: ...


class StubDetectorBackend:
    """A deterministic backend for tests and the model-free path.

    It returns whatever detections were registered for a frame id, and nothing
    for an unknown frame, so 'object present' and 'object absent' are both exact
    and repeatable without any model weights.
    """

    def __init__(self, detections_by_frame_id: dict[str, Sequence[Detection]] | None = None) -> None:
        self._by_frame_id = {key: tuple(value) for key, value in (detections_by_frame_id or {}).items()}

    def detect(self, frame: CameraFrame) -> Sequence[Detection]:
        return self._by_frame_id.get(frame.frame_id or "", ())


class DetectorAdapter:
    """Wrap a backend and turn a frame into a validated, fail-closed result."""

    def __init__(self, backend: DetectorBackend, *, max_age_s: float = DEFAULT_MAX_AGE_S, source: str | None = None) -> None:
        self._backend = backend
        self._max_age_s = max_age_s
        self._source = source

    def analyze(self, frame: CameraFrame | None) -> DetectionResult:
        """Detect on a frame, or record the honest absence of one.

        A missing frame yields a MISSING result; an unreadable frame or a backend
        that raises yields an INVALID one. Neither carries detections, so absence
        is never mistaken for 'nothing there'.
        """
        if frame is None:
            return self._empty_result(1, 1, None, "missing")
        if not frame.is_well_formed():
            # A raw frame whose bytes do not match its dimensions, or an empty
            # one, is not a picture of anything and must not reach the backend.
            return self._empty_result(frame.width, frame.height, frame.frame_id, "invalid")

        try:
            detected = tuple(self._backend.detect(frame))
        except Exception:  # noqa: BLE001 - any backend failure must fail closed, not propagate
            return self._empty_result(frame.width, frame.height, frame.frame_id, "invalid")

        result = DetectionResult(
            captured_at=_utc(frame.captured_at),
            max_age_s=self._max_age_s,
            declared_validity="valid",
            frame_width=frame.width,
            frame_height=frame.height,
            frame_id=frame.frame_id,
            detections=detected,
            source=self._source,
        )
        # The adapter validates its own output against the contract, so a
        # backend that returns an off-frame or over-confident box fails closed
        # rather than reaching a consumer.
        try:
            validate_detection_document(result.to_document())
            _check_boxes_within_frame(result)
        except DetectionContractError:
            return self._empty_result(frame.width, frame.height, frame.frame_id, "invalid")
        return result

    def _empty_result(self, width: int, height: int, frame_id: str | None, validity: str) -> DetectionResult:
        return DetectionResult(
            captured_at=datetime.now(UTC),
            max_age_s=self._max_age_s,
            declared_validity=validity,
            frame_width=width or 1,
            frame_height=height or 1,
            frame_id=frame_id,
            detections=(),
            source=self._source,
        )


@lru_cache(maxsize=1)
def _schema() -> dict[str, Any]:
    try:
        return json.loads(DETECTION_SCHEMA_PATH.read_text(encoding="utf-8"))
    except OSError as error:
        raise DetectionContractError(
            f"Cannot read the detection schema '{DETECTION_SCHEMA_PATH}': {error.strerror}."
        ) from error


def validate_detection_document(document: object) -> None:
    """Check a detection document against the versioned contract."""
    try:
        jsonschema.validate(document, _schema())
    except jsonschema.ValidationError as error:
        location = "/".join(str(part) for part in error.absolute_path) or "<root>"
        raise DetectionContractError(f"Detection rejected at '{location}': {error.message}") from error


def _check_boxes_within_frame(result: DetectionResult) -> None:
    for detection in result.detections:
        box = detection.bbox
        if box.x + box.width > result.frame_width or box.y + box.height > result.frame_height:
            raise DetectionContractError(
                f"Detection '{detection.label}' box extends past the {result.frame_width}x{result.frame_height} frame."
            )


def _utc(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise DetectionContractError("A detection time must be timezone-aware to measure an age.")
    return moment.astimezone(UTC)
