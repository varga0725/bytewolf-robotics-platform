"""Canonical immutable observation events for external Vision consumers.

These are the versioned contracts the Vision stack publishes to the Cognitive
Hook Runtime, the world model and the dashboard. They are observation-only: an
event describes what was seen, never a command, and it carries everything a
consumer needs to judge it -- the source frame, the producing model and version,
a confidence, a timestamp and a TTL, and an optional evidence artifact.

Each event validates itself on construction and fails closed: an ill-formed
event raises ``VisionContractError`` with a message naming the offending field,
rather than being quietly accepted. Freshness is derived by the consumer from
``observed_at`` and ``ttl``, so a slow pipeline cannot pass stale evidence off as
current.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import (
    BoundingBox,
    CameraFrame,
    DetectionResult,
    ResultState,
    VisionContractError,
    _positive_duration,
    _require_aware,
    _unit_interval,
)


# -- shared validation ----------------------------------------------------

def _require_tag(value: object, expected: str, what: str) -> None:
    """The contract_version must match exactly, so a consumer never guesses."""
    if value != expected:
        raise VisionContractError(f"{what} contract_version must be {expected!r}.")


def _require_text(value: object, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise VisionContractError(f"{name} must be a non-empty string.")


def _require_confidence(value: object) -> None:
    if not _unit_interval(value):
        raise VisionContractError("confidence must be a number in [0, 1].")


def _require_ttl(value: object) -> None:
    if not _positive_duration(value):
        raise VisionContractError("ttl must be a positive duration.")


def _box_within_frame(frame: CameraFrame, box: BoundingBox) -> bool:
    return (
        isinstance(frame, CameraFrame)
        and box.x_px + box.width_px <= frame.width_px
        and box.y_px + box.height_px <= frame.height_px
    )


def _require_box_in_frame(frame: CameraFrame, box: object) -> None:
    if not isinstance(box, BoundingBox):
        raise VisionContractError("bounding_box must be a BoundingBox.")
    if not _box_within_frame(frame, box):
        raise VisionContractError("bounding_box must lie within the source frame.")


def _require_artifact_matches_frame(artifact: object, frame: CameraFrame, what: str) -> None:
    if artifact is None:
        return
    if not isinstance(artifact, VideoArtifactRef):
        raise VisionContractError(f"{what} artifact must be a VideoArtifactRef.")
    if artifact.source_frame != frame:
        raise VisionContractError(f"{what} artifact must reference the source frame.")


def _freshness(observed_at: datetime, now: datetime, ttl: timedelta) -> ResultState:
    """Fresh until ``ttl`` has elapsed since ``observed_at``; only the consumer
    knows the current time, so staleness is derived here, not declared."""
    _require_aware(now, "now")
    age = now.astimezone() - observed_at.astimezone()
    return ResultState.STALE if age > ttl else ResultState.VALID


# -- canonical events -----------------------------------------------------

@dataclass(frozen=True)
class VideoArtifactRef:
    """A reference to a stored evidence clip or keyframe for one source frame."""

    contract_version: str
    artifact_id: str
    source_frame: CameraFrame
    encoding: str
    payload_hash: str
    created_at: datetime
    ttl: timedelta

    def __post_init__(self) -> None:
        _require_tag(self.contract_version, "video_artifact_ref.v1", "VideoArtifactRef")
        _require_text(self.artifact_id, "artifact_id")
        if self.encoding not in {"jpeg", "h264", "h265"}:
            raise VisionContractError("encoding must be one of jpeg, h264, h265.")
        if self.payload_hash != self.source_frame.payload_hash:
            raise VisionContractError("payload_hash must match the source frame's hash.")
        _require_ttl(self.ttl)
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True)
class DetectionEvent:
    """One detection in one frame, with the model that produced it."""

    contract_version: str
    event_id: str
    source_frame: CameraFrame
    model_id: str
    model_version: str
    label: str
    confidence: float
    bounding_box: BoundingBox
    observed_at: datetime
    ttl: timedelta
    artifact: VideoArtifactRef | None = None

    def __post_init__(self) -> None:
        _require_tag(self.contract_version, "detection_event.v1", "DetectionEvent")
        _require_text(self.event_id, "event_id")
        _require_text(self.model_id, "model_id")
        _require_text(self.model_version, "model_version")
        _require_text(self.label, "label")
        _require_confidence(self.confidence)
        _require_box_in_frame(self.source_frame, self.bounding_box)
        _require_ttl(self.ttl)
        _require_aware(self.observed_at, "observed_at")
        _require_artifact_matches_frame(self.artifact, self.source_frame, "Detection event")

    def state(self, now: datetime) -> ResultState:
        """Whether this detection may still be acted on at ``now``."""
        return _freshness(self.observed_at, now, self.ttl)


@dataclass(frozen=True)
class TrackedObject:
    """A detection carried across frames under a stable tracker id."""

    contract_version: str
    source_frame: CameraFrame
    tracker_id: str
    model_id: str
    model_version: str
    label: str
    confidence: float
    bounding_box: BoundingBox
    observed_at: datetime
    ttl: timedelta
    artifact: VideoArtifactRef | None = None

    def __post_init__(self) -> None:
        _require_tag(self.contract_version, "tracked_object.v1", "TrackedObject")
        _require_text(self.tracker_id, "tracker_id")
        _require_text(self.model_id, "model_id")
        _require_text(self.model_version, "model_version")
        _require_text(self.label, "label")
        _require_confidence(self.confidence)
        _require_box_in_frame(self.source_frame, self.bounding_box)
        _require_ttl(self.ttl)
        _require_aware(self.observed_at, "observed_at")
        _require_artifact_matches_frame(self.artifact, self.source_frame, "Tracked object")

    def state(self, now: datetime) -> ResultState:
        """Whether this track may still be acted on at ``now``."""
        return _freshness(self.observed_at, now, self.ttl)


@dataclass(frozen=True)
class VisionSummary:
    """The consumer-facing roll-up of one frame's detections and tracks."""

    contract_version: str
    source_frame: CameraFrame
    model_id: str
    model_version: str
    observed_at: datetime
    ttl: timedelta
    state_value: ResultState
    events: tuple[DetectionEvent, ...]
    tracks: tuple[TrackedObject, ...]

    def __post_init__(self) -> None:
        _require_tag(self.contract_version, "vision_summary.v1", "VisionSummary")
        _require_text(self.model_id, "model_id")
        _require_text(self.model_version, "model_version")
        if not isinstance(self.state_value, ResultState):
            raise VisionContractError("state_value must be a ResultState.")
        if not isinstance(self.events, tuple):
            raise VisionContractError("events must be a tuple.")
        if not isinstance(self.tracks, tuple):
            raise VisionContractError("tracks must be a tuple.")
        _require_ttl(self.ttl)
        _require_aware(self.observed_at, "observed_at")

    def state(self, now: datetime) -> ResultState:
        """A producer-declared non-valid state is kept; otherwise freshness
        decides, so a summary trusted at production still expires by its TTL."""
        if self.state_value is not ResultState.VALID:
            return self.state_value
        return _freshness(self.observed_at, now, self.ttl)


# -- adapter --------------------------------------------------------------

def canonical_from_detection_result(result: DetectionResult, *, ttl: timedelta) -> VisionSummary:
    """Build the canonical summary from a pipeline DetectionResult.

    Every detection becomes a DetectionEvent; only detections that carry a
    tracker id become TrackedObjects, so an untracked detection never
    fabricates a track.
    """
    if not isinstance(result, DetectionResult):
        raise VisionContractError("Canonical adapter requires a DetectionResult.")
    _require_ttl(ttl)

    frame = result.frame
    events = tuple(
        DetectionEvent(
            "detection_event.v1",
            f"{frame.stream_session_id}:{frame.frame_sequence}:{index}",
            frame,
            result.model_id,
            result.model_version,
            detection.label,
            detection.confidence,
            detection.bounding_box,
            result.produced_at,
            ttl,
        )
        for index, detection in enumerate(result.detections)
    )
    tracks = tuple(
        TrackedObject(
            "tracked_object.v1",
            frame,
            detection.tracker_id,
            result.model_id,
            result.model_version,
            detection.label,
            detection.confidence,
            detection.bounding_box,
            result.produced_at,
            ttl,
        )
        for detection in result.detections
        if detection.tracker_id is not None
    )
    return VisionSummary(
        "vision_summary.v1",
        frame,
        result.model_id,
        result.model_version,
        result.produced_at,
        ttl,
        ResultState.VALID,
        events,
        tracks,
    )
