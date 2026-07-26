"""Deterministic local IoU tracking for observation-only Vision Core fallback use.

This deliberately small tracker is a reference implementation behind
``TrackerPort``.  It associates detector observations only; it has no command,
actuator, simulator, or flight-control dependency.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import isfinite

from .contracts import BoundingBox, CameraFrame, Detection, VisionContractError


@dataclass(frozen=True)
class _Track:
    """Internal immutable state for one active local observation track."""

    tracker_id: str
    label: str
    bounding_box: BoundingBox
    missed_frames: int = 0


class IoUAssociationTracker:
    """Greedy, deterministic IoU association with explicit track expiry.

    IDs are local opaque tokens, stable only while the observation remains
    associated.  A track expires after more than ``maximum_missed_frames``
    calls without a matching observation; a later observation is a distinct
    reacquisition and receives a new ID.
    """

    def __init__(self, *, iou_threshold: float = 0.3, maximum_missed_frames: int = 5) -> None:
        if not isinstance(iou_threshold, (int, float)) or isinstance(iou_threshold, bool) or not isfinite(iou_threshold) or not 0.0 < iou_threshold <= 1.0:
            raise VisionContractError("IoU threshold must be a finite number greater than zero and at most one.")
        if type(maximum_missed_frames) is not int or maximum_missed_frames < 0:
            raise VisionContractError("maximum_missed_frames must be a non-negative integer.")
        self._iou_threshold = float(iou_threshold)
        self._maximum_missed_frames = maximum_missed_frames
        self._tracks: tuple[_Track, ...] = ()
        self._next_identifier = 1
        self._expired_track_ids: tuple[str, ...] = ()

    @property
    def expired_track_ids(self) -> tuple[str, ...]:
        """Opaque IDs that expired during the most recent tracking call."""
        return self._expired_track_ids

    def track(self, detections: tuple[Detection, ...], frame: CameraFrame) -> tuple[Detection, ...]:
        """Return immutable observations annotated with local opaque track IDs."""
        self._validate_observations(detections, frame)
        assignments = self._associate(detections)
        updated_tracks: list[_Track] = []
        expired_track_ids: list[str] = []

        for index, track in enumerate(self._tracks):
            detection_index = next((candidate for candidate, matched in assignments.items() if matched == index), None)
            if detection_index is None:
                advanced = replace(track, missed_frames=track.missed_frames + 1)
                if advanced.missed_frames <= self._maximum_missed_frames:
                    updated_tracks.append(advanced)
                else:
                    expired_track_ids.append(track.tracker_id)
            else:
                detection = detections[detection_index]
                updated_tracks.append(replace(track, label=detection.label, bounding_box=detection.bounding_box, missed_frames=0))

        output: list[Detection] = []
        for detection_index, detection in enumerate(detections):
            track_index = assignments.get(detection_index)
            if track_index is None:
                tracker_id = self._allocate_identifier()
                updated_tracks.append(_Track(tracker_id, detection.label, detection.bounding_box))
            else:
                tracker_id = self._tracks[track_index].tracker_id
            output.append(replace(detection, tracker_id=tracker_id))

        self._tracks = tuple(updated_tracks)
        self._expired_track_ids = tuple(expired_track_ids)
        return tuple(output)

    def _associate(self, detections: tuple[Detection, ...]) -> dict[int, int]:
        candidates: list[tuple[float, int, int]] = []
        for track_index, track in enumerate(self._tracks):
            for detection_index, detection in enumerate(detections):
                if track.label != detection.label:
                    continue
                overlap = _iou(track.bounding_box, detection.bounding_box)
                if overlap >= self._iou_threshold:
                    candidates.append((-overlap, track_index, detection_index))
        assignments: dict[int, int] = {}
        occupied_tracks: set[int] = set()
        for _negative_overlap, track_index, detection_index in sorted(candidates):
            if track_index not in occupied_tracks and detection_index not in assignments:
                assignments[detection_index] = track_index
                occupied_tracks.add(track_index)
        return assignments

    @staticmethod
    def _validate_observations(detections: tuple[Detection, ...], frame: CameraFrame) -> None:
        if not isinstance(frame, CameraFrame):
            raise VisionContractError("Tracking requires a CameraFrame contract.")
        if not isinstance(detections, tuple):
            raise VisionContractError("Tracking requires an immutable tuple of Detection contracts.")
        for detection in detections:
            if not isinstance(detection, Detection):
                raise VisionContractError("Tracking requires the Detection contract for every observation.")
            if detection.tracker_id is not None:
                raise VisionContractError("Local IoU tracking requires unassigned detector observations.")
            box = detection.bounding_box
            if box.x_px + box.width_px > frame.width_px or box.y_px + box.height_px > frame.height_px:
                raise VisionContractError("Detection bounding box exceeds source frame.")

    def _allocate_identifier(self) -> str:
        identifier = f"local-{self._next_identifier:06d}"
        self._next_identifier += 1
        return identifier


def _iou(left: BoundingBox, right: BoundingBox) -> float:
    """Return intersection-over-union for two positive-area pixel boxes."""
    intersection_left = max(left.x_px, right.x_px)
    intersection_top = max(left.y_px, right.y_px)
    intersection_right = min(left.x_px + left.width_px, right.x_px + right.width_px)
    intersection_bottom = min(left.y_px + left.height_px, right.y_px + right.height_px)
    intersection_width = max(0, intersection_right - intersection_left)
    intersection_height = max(0, intersection_bottom - intersection_top)
    intersection = intersection_width * intersection_height
    union = left.width_px * left.height_px + right.width_px * right.height_px - intersection
    return intersection / union
