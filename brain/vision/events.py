"""Canonical immutable observation events for external Vision consumers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .contracts import BoundingBox, CameraFrame, DetectionResult, ResultState, VisionContractError, _positive_duration, _require_aware, _unit_interval


def _fresh(observed_at: datetime, now: datetime, ttl: timedelta) -> ResultState:
    _require_aware(now, "now")
    return ResultState.STALE if now.astimezone() - observed_at.astimezone() > ttl else ResultState.VALID


@dataclass(frozen=True)
class VideoArtifactRef:
    contract_version: str
    artifact_id: str
    source_frame: CameraFrame
    encoding: str
    payload_hash: str
    created_at: datetime
    ttl: timedelta

    def __post_init__(self) -> None:
        if self.contract_version != "video_artifact_ref.v1" or not isinstance(self.artifact_id, str) or not self.artifact_id.strip() or self.encoding not in {"jpeg", "h264", "h265"} or self.payload_hash != self.source_frame.payload_hash or not _positive_duration(self.ttl):
            raise VisionContractError("Invalid video artifact reference.")
        _require_aware(self.created_at, "created_at")


@dataclass(frozen=True)
class DetectionEvent:
    contract_version: str; event_id: str; source_frame: CameraFrame; model_id: str; model_version: str; label: str; confidence: float; bounding_box: BoundingBox; observed_at: datetime; ttl: timedelta; artifact: VideoArtifactRef | None = None
    def __post_init__(self) -> None:
        if self.contract_version != "detection_event.v1" or not all(isinstance(v, str) and v.strip() for v in (self.event_id, self.model_id, self.model_version, self.label)) or not _unit_interval(self.confidence) or not isinstance(self.bounding_box, BoundingBox) or not _positive_duration(self.ttl) or (self.artifact is not None and not isinstance(self.artifact, VideoArtifactRef)):
            raise VisionContractError("Invalid detection event.")
        if self.artifact is not None and self.artifact.source_frame != self.source_frame:
            raise VisionContractError("Detection event artifact must reference the source frame.")
        _require_aware(self.observed_at, "observed_at")
    def state(self, now: datetime) -> ResultState: return _fresh(self.observed_at, now, self.ttl)


@dataclass(frozen=True)
class TrackedObject:
    contract_version: str; source_frame: CameraFrame; tracker_id: str; model_id: str; model_version: str; label: str; confidence: float; bounding_box: BoundingBox; observed_at: datetime; ttl: timedelta; artifact: VideoArtifactRef | None = None
    def __post_init__(self) -> None:
        if self.contract_version != "tracked_object.v1" or not all(isinstance(v, str) and v.strip() for v in (self.tracker_id, self.model_id, self.model_version, self.label)) or not _unit_interval(self.confidence) or not isinstance(self.bounding_box, BoundingBox) or not _positive_duration(self.ttl): raise VisionContractError("Invalid tracked object.")
        _require_aware(self.observed_at, "observed_at")
        if self.artifact is not None and self.artifact.source_frame != self.source_frame:
            raise VisionContractError("Tracked object artifact must reference the source frame.")
    def state(self, now: datetime) -> ResultState: return _fresh(self.observed_at, now, self.ttl)


@dataclass(frozen=True)
class VisionSummary:
    contract_version: str; source_frame: CameraFrame; model_id: str; model_version: str; observed_at: datetime; ttl: timedelta; state_value: ResultState; events: tuple[DetectionEvent, ...]; tracks: tuple[TrackedObject, ...]

    def __post_init__(self) -> None:
        if self.contract_version != "vision_summary.v1" or not all(isinstance(v, str) and v.strip() for v in (self.model_id, self.model_version)) or not isinstance(self.state_value, ResultState) or not isinstance(self.events, tuple) or not isinstance(self.tracks, tuple) or not _positive_duration(self.ttl): raise VisionContractError("Invalid vision summary.")
        _require_aware(self.observed_at, "observed_at")

    def state(self, now: datetime) -> ResultState:
        return self.state_value if self.state_value is not ResultState.VALID else _fresh(self.observed_at, now, self.ttl)


def canonical_from_detection_result(result: DetectionResult, *, ttl: timedelta) -> VisionSummary:
    if not isinstance(result, DetectionResult) or not _positive_duration(ttl): raise VisionContractError("Canonical adapter requires DetectionResult and positive TTL.")
    events = tuple(DetectionEvent("detection_event.v1", f"{result.frame.stream_session_id}:{result.frame.frame_sequence}:{index}", result.frame, result.model_id, result.model_version, item.label, item.confidence, item.bounding_box, result.produced_at, ttl) for index, item in enumerate(result.detections))
    tracks = tuple(TrackedObject("tracked_object.v1", result.frame, item.tracker_id, result.model_id, result.model_version, item.label, item.confidence, item.bounding_box, result.produced_at, ttl) for item in result.detections if item.tracker_id is not None)
    return VisionSummary("vision_summary.v1", result.frame, result.model_id, result.model_version, result.produced_at, ttl, ResultState.VALID, events, tracks)
