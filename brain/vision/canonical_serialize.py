"""Serialise internal Vision events to the shared cross-consumer contracts.

The internal Vision dataclasses are rich; a consumer reads only the shared,
versioned contract in ``shared/schemas/vision/``. These functions produce that
consumer-facing view, so the Vision stack conforms to the exact contract the
Cognitive Runtime validates on read -- the producer's internal CameraFrame
details (calibration, latency, drop counters) stay inside Vision, and freshness
is expressed as ``max_age_s`` for the consumer to resolve.
"""

from __future__ import annotations

from typing import Any

from .contracts import BoundingBox, CameraFrame, ResultState
from .events import DetectionEvent, VideoArtifactRef, VisionSummary


def _source(frame: CameraFrame) -> str:
    return f"camera:{frame.camera_id}"


def _box(box: BoundingBox) -> dict[str, Any]:
    return {"x_px": box.x_px, "y_px": box.y_px, "width_px": box.width_px, "height_px": box.height_px}


def _box_key(box: BoundingBox) -> tuple[int, int, int, int]:
    return (box.x_px, box.y_px, box.width_px, box.height_px)


def _validity(state: ResultState) -> str:
    # The producer declares valid/invalid/missing; 'stale' is derived by the
    # consumer from max_age_s, so a producer-declared STALE publishes as 'valid'
    # and the consumer re-derives staleness from the age it alone knows.
    if state is ResultState.MISSING:
        return "missing"
    if state is ResultState.INVALID:
        return "invalid"
    return "valid"


def _artifact_ref(artifact: VideoArtifactRef | None) -> dict[str, Any] | None:
    if artifact is None:
        return None
    return {
        "artifact_id": artifact.artifact_id,
        "encoding": artifact.encoding,
        "payload_hash": artifact.payload_hash,
    }


def detection_event_to_canonical(event: DetectionEvent) -> dict[str, Any]:
    """Map one internal DetectionEvent to the shared detection_event v0.1 view."""
    frame = event.source_frame
    document: dict[str, Any] = {
        "contract_version": "v0.1",
        "event_id": event.event_id,
        "source": _source(frame),
        "observed_at": event.observed_at.isoformat(),
        "max_age_s": event.ttl.total_seconds(),
        "validity": "valid",
        "frame": {"width_px": frame.width_px, "height_px": frame.height_px},
        "model_id": event.model_id,
        "model_version": event.model_version,
        "label": event.label,
        "confidence": event.confidence,
        "bounding_box": _box(event.bounding_box),
    }
    artifact_ref = _artifact_ref(event.artifact)
    if artifact_ref is not None:
        document["artifact_ref"] = artifact_ref
    return document


def vision_summary_to_canonical(summary: VisionSummary) -> dict[str, Any]:
    """Map an internal VisionSummary to the shared vision_summary v0.1 view.

    The internal summary keeps events and tracks separately; the consumer view is
    one bounded list of detection identities, each carrying a tracker id when the
    detection is also tracked. detection_count is the true number of detections.
    """
    frame = summary.source_frame
    tracker_by_detection = {
        (track.label, _box_key(track.bounding_box)): track.tracker_id
        for track in summary.tracks
    }
    detections: list[dict[str, Any]] = []
    for event in summary.events:
        detection: dict[str, Any] = {
            "event_id": event.event_id,
            "label": event.label,
            "confidence": event.confidence,
            "bounding_box": _box(event.bounding_box),
        }
        tracker_id = tracker_by_detection.get((event.label, _box_key(event.bounding_box)))
        if tracker_id is not None:
            detection["tracker_id"] = tracker_id
        detections.append(detection)
    return {
        "contract_version": "v0.1",
        "source": _source(frame),
        "observed_at": summary.observed_at.isoformat(),
        "max_age_s": summary.ttl.total_seconds(),
        "validity": _validity(summary.state_value),
        "frame": {"width_px": frame.width_px, "height_px": frame.height_px},
        "model_id": summary.model_id,
        "model_version": summary.model_version,
        "detections": detections,
        "detection_count": len(summary.events),
    }
