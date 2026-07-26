"""Versioned, immutable, observation-only contracts for the Vision Core.

These objects deliberately describe sensor evidence only.  They contain no
command, actuator, or flight-control concept, and consumers must treat every
state other than :attr:`ResultState.VALID` as unusable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from math import isfinite
import re


CAMERA_FRAME_V1 = "camera_frame.v1"
DETECTION_RESULT_V1 = "detection_result.v1"
VISION_HEALTH_V1 = "vision_health.v1"
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SUPPORTED_ENCODINGS = frozenset(("jpeg", "h264", "h265", "rgb8"))


class VisionContractError(ValueError):
    """Raised for an internally malformed Vision contract document."""


class ResultState(str, Enum):
    """The four states a Vision consumer can distinguish, never infer."""

    VALID = "valid"
    MISSING = "missing"
    STALE = "stale"
    INVALID = "invalid"

    @property
    def usable(self) -> bool:
        return self is ResultState.VALID


@dataclass(frozen=True)
class CameraFrame:
    """Identity-preserving metadata for one camera payload.

    Payload bytes deliberately stay outside this contract.  ``payload_hash``
    binds this metadata to those bytes without causing the dashboard or audit
    metadata store to copy video frames.
    """

    contract_version: str
    device_id: str
    camera_id: str
    stream_session_id: str
    frame_sequence: int
    captured_at: datetime
    received_at: datetime
    calibration_version: str
    payload_hash: str
    encoding: str
    width_px: int
    height_px: int
    latency_ms: float
    dropped_frames: int


@dataclass(frozen=True)
class FrameValidation:
    """An explicit validation outcome; malformed evidence is never a miss."""

    state: ResultState
    frame: CameraFrame | None
    reason: str = ""

    @classmethod
    def missing(cls, reason: str = "camera frame was not received") -> FrameValidation:
        return cls(ResultState.MISSING, None, reason)

    @property
    def usable(self) -> bool:
        return self.state.usable


@dataclass(frozen=True)
class _SequenceEntry:
    device_id: str
    camera_id: str
    stream_session_id: str
    frame_sequence: int
    payload_hash: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return (self.device_id, self.camera_id, self.stream_session_id)


@dataclass(frozen=True)
class FrameSequenceLedger:
    """Immutable anti-replay state for accepted frames.

    Callers replace their old ledger with the returned ledger.  Invalid frames
    never advance it, so a malformed packet cannot make later valid evidence
    look like a replay.
    """

    entries: tuple[_SequenceEntry, ...] = ()
    maximum_entries: int = 256

    def last_sequence(self, frame: CameraFrame) -> int | None:
        entry = self._entry_for(frame)
        return entry.frame_sequence if entry else None

    def validate(
        self,
        frame: CameraFrame,
        *,
        now: datetime,
        max_frame_age: timedelta = timedelta(seconds=1),
        max_clock_skew: timedelta = timedelta(seconds=2),
    ) -> tuple[FrameValidation, FrameSequenceLedger]:
        """Validate one frame and return an updated ledger only on acceptance."""
        reason = _frame_error(frame, now=now, max_clock_skew=max_clock_skew)
        if reason:
            return FrameValidation(ResultState.INVALID, frame, reason), self
        if not _positive_duration(max_frame_age) or not _positive_duration(max_clock_skew):
            raise VisionContractError("Frame age and clock skew limits must be positive durations.")

        entry = self._entry_for(frame)
        if entry is not None:
            if frame.frame_sequence == entry.frame_sequence:
                return FrameValidation(ResultState.INVALID, frame, "frame replay detected"), self
            if frame.frame_sequence < entry.frame_sequence:
                return FrameValidation(ResultState.INVALID, frame, "frame sequence regressed"), self

        age = max(now.astimezone(UTC) - frame.captured_at.astimezone(UTC), timedelta())
        updated = self._replace(
            _SequenceEntry(
                frame.device_id,
                frame.camera_id,
                frame.stream_session_id,
                frame.frame_sequence,
                frame.payload_hash,
            )
        )
        if age > max_frame_age:
            return FrameValidation(ResultState.STALE, frame, "frame exceeds freshness limit"), updated
        return FrameValidation(ResultState.VALID, frame), updated

    def _entry_for(self, frame: CameraFrame) -> _SequenceEntry | None:
        identity = (frame.device_id, frame.camera_id, frame.stream_session_id)
        return next((entry for entry in self.entries if entry.identity == identity), None)

    def _replace(self, replacement: _SequenceEntry) -> FrameSequenceLedger:
        if type(self.maximum_entries) is not int or self.maximum_entries <= 0:
            raise VisionContractError("Sequence ledger maximum_entries must be a positive integer.")
        entries = tuple(entry for entry in self.entries if entry.identity != replacement.identity)
        bounded = (entries + (replacement,))[-self.maximum_entries :]
        return FrameSequenceLedger(bounded, self.maximum_entries)


@dataclass(frozen=True)
class BoundingBox:
    """A pixel bounding box with an origin at the upper-left of its source frame."""

    x_px: int
    y_px: int
    width_px: int
    height_px: int

    def __post_init__(self) -> None:
        if any(type(value) is not int for value in (self.x_px, self.y_px, self.width_px, self.height_px)):
            raise VisionContractError("Bounding-box coordinates must be integers.")
        if self.x_px < 0 or self.y_px < 0 or self.width_px <= 0 or self.height_px <= 0:
            raise VisionContractError("Bounding boxes must have non-negative origins and positive dimensions.")


@dataclass(frozen=True)
class Detection:
    """One detector observation associated with an optional tracker identity."""

    label: str
    confidence: float
    bounding_box: BoundingBox
    tracker_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.label, str) or not self.label.strip():
            raise VisionContractError("Detection label is required.")
        if not _unit_interval(self.confidence):
            raise VisionContractError("Detection confidence must be a finite number between zero and one.")
        if self.tracker_id is not None and (not isinstance(self.tracker_id, str) or not self.tracker_id.strip()):
            raise VisionContractError("A tracker ID must be a non-empty string when supplied.")


@dataclass(frozen=True)
class DetectionResult:
    """Versioned detector/tracker output bound to the exact input frame."""

    contract_version: str
    frame: CameraFrame
    model_id: str
    model_version: str
    produced_at: datetime
    detections: tuple[Detection, ...]

    def __post_init__(self) -> None:
        if self.contract_version != DETECTION_RESULT_V1:
            raise VisionContractError(f"Unsupported detection-result version: {self.contract_version!r}.")
        if not isinstance(self.model_id, str) or not self.model_id.strip() or not isinstance(self.model_version, str) or not self.model_version.strip():
            raise VisionContractError("Detection results require model ID and version.")
        _require_aware(self.produced_at, "produced_at")
        source_error = _frame_error(
            self.frame,
            now=self.produced_at,
            max_clock_skew=timedelta(seconds=2),
        )
        if source_error:
            raise VisionContractError(f"Detection result has an invalid source frame: {source_error}")
        if self.produced_at < self.frame.captured_at:
            raise VisionContractError("A detection cannot be produced before its frame was captured.")
        if not isinstance(self.detections, tuple):
            raise VisionContractError("Detections must be an immutable tuple.")
        for detection in self.detections:
            if not isinstance(detection, Detection):
                raise VisionContractError("Detections must use the Detection contract.")
            right = detection.bounding_box.x_px + detection.bounding_box.width_px
            bottom = detection.bounding_box.y_px + detection.bounding_box.height_px
            if right > self.frame.width_px or bottom > self.frame.height_px:
                raise VisionContractError("Detection bounding box exceeds its source frame.")

    def state(
        self,
        now: datetime,
        *,
        max_result_age: timedelta = timedelta(seconds=1),
        max_source_frame_age: timedelta = timedelta(seconds=1),
    ) -> ResultState:
        _require_aware(now, "now")
        if not _positive_duration(max_result_age) or not _positive_duration(max_source_frame_age):
            raise VisionContractError("Result and source-frame freshness limits must be positive durations.")
        age = max(now.astimezone(UTC) - self.produced_at.astimezone(UTC), timedelta())
        source_age = max(now.astimezone(UTC) - self.frame.captured_at.astimezone(UTC), timedelta())
        return ResultState.STALE if age > max_result_age or source_age > max_source_frame_age else ResultState.VALID


_HEALTH_VALUES = frozenset(("healthy", "degraded", "unavailable", "missing"))


@dataclass(frozen=True)
class VisionHealth:
    """Health evidence for the read-only dashboard and operational alerts."""

    observed_at: datetime
    stream_state: str
    model_state: str
    gpu_state: str
    backlog_frames: int
    dropped_frames: int
    contract_version: str = VISION_HEALTH_V1

    def __post_init__(self) -> None:
        if self.contract_version != VISION_HEALTH_V1:
            raise VisionContractError(f"Unsupported vision-health version: {self.contract_version!r}.")
        _require_aware(self.observed_at, "observed_at")
        if any(value not in _HEALTH_VALUES for value in (self.stream_state, self.model_state, self.gpu_state)):
            raise VisionContractError("Health components must be healthy, degraded, unavailable, or missing.")
        if type(self.backlog_frames) is not int or type(self.dropped_frames) is not int:
            raise VisionContractError("Health frame counters must be integers.")
        if self.backlog_frames < 0 or self.dropped_frames < 0:
            raise VisionContractError("Health frame counters cannot be negative.")

    def state(self, now: datetime, *, max_age: timedelta = timedelta(seconds=2)) -> ResultState:
        _require_aware(now, "now")
        if not _positive_duration(max_age):
            raise VisionContractError("Health freshness limit must be a positive duration.")
        if "missing" in (self.stream_state, self.model_state, self.gpu_state):
            return ResultState.MISSING
        if "unavailable" in (self.stream_state, self.model_state, self.gpu_state):
            return ResultState.INVALID
        age = max(now.astimezone(UTC) - self.observed_at.astimezone(UTC), timedelta())
        return ResultState.STALE if age > max_age else ResultState.VALID


def _frame_error(frame: CameraFrame, *, now: datetime, max_clock_skew: timedelta) -> str | None:
    if not isinstance(frame, CameraFrame):
        return "input is not a CameraFrame contract"
    if frame.contract_version != CAMERA_FRAME_V1:
        return "unsupported camera-frame version"
    if not _positive_duration(max_clock_skew):
        raise VisionContractError("Clock skew limit must be a positive duration.")
    if any(not isinstance(value, str) or not value.strip() for value in (frame.device_id, frame.camera_id, frame.stream_session_id, frame.calibration_version)):
        return "camera-frame identity fields are required"
    if type(frame.frame_sequence) is not int or frame.frame_sequence < 0:
        return "frame sequence must be a non-negative integer"
    if not isinstance(frame.payload_hash, str) or not _SHA256.fullmatch(frame.payload_hash):
        return "payload hash must be a lowercase SHA-256 digest"
    if frame.encoding not in _SUPPORTED_ENCODINGS:
        return "unsupported frame encoding"
    if type(frame.width_px) is not int or type(frame.height_px) is not int or frame.width_px <= 0 or frame.height_px <= 0:
        return "frame dimensions must be positive integers"
    if not _nonnegative_finite(frame.latency_ms):
        return "frame latency must be a finite non-negative number"
    if type(frame.dropped_frames) is not int or frame.dropped_frames < 0:
        return "dropped-frame count must be a non-negative integer"
    try:
        _require_aware(frame.captured_at, "captured_at")
        _require_aware(frame.received_at, "received_at")
        _require_aware(now, "now")
    except VisionContractError as error:
        return str(error)
    if frame.received_at < frame.captured_at:
        return "frame received before capture"
    if frame.captured_at.astimezone(UTC) - now.astimezone(UTC) > max_clock_skew:
        return "frame capture timestamp is too far in the future"
    if frame.received_at.astimezone(UTC) - now.astimezone(UTC) > max_clock_skew:
        return "frame received timestamp is too far in the future"
    return None


def _require_aware(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise VisionContractError(f"{name} must be a timezone-aware datetime.")


def _positive_duration(value: object) -> bool:
    return isinstance(value, timedelta) and value > timedelta()


def _nonnegative_finite(value: object) -> bool:
    return type(value) in (int, float) and isfinite(float(value)) and float(value) >= 0.0


def _unit_interval(value: object) -> bool:
    return _nonnegative_finite(value) and float(value) <= 1.0
