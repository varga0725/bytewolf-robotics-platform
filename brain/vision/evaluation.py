"""Deterministic, fail-closed ground-truth evaluation for Vision benchmarks.

The evaluator consumes only immutable observation contracts.  It deliberately
does not accept commands, vehicle state, or any actuator dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable

from brain.vision.benchmark import BenchmarkSample
from brain.vision.contracts import BoundingBox, Detection, DetectionResult


class GroundTruthValidationError(ValueError):
    """Raised when benchmark labels are incomplete or ambiguous.

    A benchmark must not silently convert suspect labels into a score: callers
    should fix or explicitly exclude the source material first.
    """


@dataclass(frozen=True)
class GroundTruth:
    """One labelled object in the exact source frame being evaluated."""

    target_id: str
    label: str
    bounding_box: BoundingBox

    def __post_init__(self) -> None:
        if not isinstance(self.target_id, str) or not self.target_id.strip():
            raise GroundTruthValidationError("ground-truth target_id is required")
        if not isinstance(self.label, str) or not self.label.strip():
            raise GroundTruthValidationError("ground-truth label is required")
        if not isinstance(self.bounding_box, BoundingBox):
            raise GroundTruthValidationError("ground-truth bounding_box must be a BoundingBox")


@dataclass(frozen=True)
class EvaluationFrame:
    """Detector output and all ground truth for one source frame."""

    result: DetectionResult
    ground_truth: tuple[GroundTruth, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.result, DetectionResult):
            raise GroundTruthValidationError("evaluation result must be a DetectionResult")
        if not isinstance(self.ground_truth, tuple):
            raise GroundTruthValidationError("ground_truth must be an immutable tuple")
        target_ids: set[str] = set()
        for item in self.ground_truth:
            if not isinstance(item, GroundTruth):
                raise GroundTruthValidationError("ground_truth entries must be GroundTruth contracts")
            if item.target_id in target_ids:
                raise GroundTruthValidationError("ground-truth target IDs must be unique per frame")
            target_ids.add(item.target_id)


@dataclass(frozen=True)
class EvaluationReport:
    """Per-frame benchmark samples plus tracking quality counts."""

    samples: tuple[BenchmarkSample, ...]
    reacquisitions: int


@dataclass(frozen=True)
class _TrackHistory:
    matched: bool
    tracker_id: str | None


class GroundTruthEvaluator:
    """Match same-label predictions to labels using deterministic IoU matching."""

    def __init__(self, *, iou_threshold: float = 0.5) -> None:
        if type(iou_threshold) not in (int, float) or not isfinite(float(iou_threshold)) or not 0 < float(iou_threshold) <= 1:
            raise GroundTruthValidationError("iou_threshold must be a finite number in (0, 1]")
        self._iou_threshold = float(iou_threshold)

    def evaluate(self, frames: Iterable[EvaluationFrame]) -> EvaluationReport:
        collected = tuple(frames)
        if not collected:
            raise GroundTruthValidationError("at least one evaluation frame is required")
        self._validate_frame_order(collected)

        history: dict[str, _TrackHistory] = {}
        samples: list[BenchmarkSample] = []
        reacquisitions = 0
        for frame in collected:
            matches = self._match(frame.ground_truth, frame.result.detections)
            matched_truth = {truth_index for truth_index, _ in matches}
            matched_detections = {detection_index for _, detection_index in matches}
            id_switches = 0
            fragmentations = 0

            for truth_index, detection_index in matches:
                target_id = frame.ground_truth[truth_index].target_id
                tracker_id = frame.result.detections[detection_index].tracker_id
                previous = history.get(target_id)
                if previous is not None and not previous.matched:
                    fragmentations += 1
                    reacquisitions += 1
                if previous is not None and previous.tracker_id is not None and tracker_id is not None and previous.tracker_id != tracker_id:
                    id_switches += 1
                history[target_id] = _TrackHistory(True, tracker_id)

            # A target present but not matched creates a discontinuity.  Targets
            # absent from a labelled frame are intentionally not inferred.
            for truth_index, truth in enumerate(frame.ground_truth):
                if truth_index not in matched_truth:
                    previous = history.get(truth.target_id)
                    history[truth.target_id] = _TrackHistory(False, previous.tracker_id if previous else None)

            latency_ms = (frame.result.produced_at - frame.result.frame.captured_at).total_seconds() * 1000
            samples.append(BenchmarkSample(
                latency_ms=latency_ms,
                true_positives=len(matches),
                false_positives=len(frame.result.detections) - len(matched_detections),
                false_negatives=len(frame.ground_truth) - len(matched_truth),
                id_switches=id_switches,
                fragmentations=fragmentations,
                dropped_frames=frame.result.frame.dropped_frames,
            ))
        return EvaluationReport(tuple(samples), reacquisitions)

    def _match(self, truth: tuple[GroundTruth, ...], detections: tuple[Detection, ...]) -> tuple[tuple[int, int], ...]:
        """Maximize matches, then use IoU and contract order as tie-breakers."""
        candidates: dict[int, list[tuple[float, int]]] = {}
        for truth_index, item in enumerate(truth):
            for detection_index, detection in enumerate(detections):
                if item.label != detection.label:
                    continue
                overlap = iou(item.bounding_box, detection.bounding_box)
                if overlap >= self._iou_threshold:
                    candidates.setdefault(truth_index, []).append((overlap, detection_index))
        for options in candidates.values():
            options.sort(key=lambda item: (-item[0], item[1]))

        # Deterministic Kuhn augmentation guarantees maximum-cardinality
        # matching.  Candidate ordering makes the selected matching stable when
        # more than one maximum solution exists.
        detection_owner: dict[int, int] = {}

        def assign(truth_index: int, visited: set[int]) -> bool:
            for _, detection_index in candidates.get(truth_index, ()):
                if detection_index in visited:
                    continue
                visited.add(detection_index)
                current_owner = detection_owner.get(detection_index)
                if current_owner is None or assign(current_owner, visited):
                    detection_owner[detection_index] = truth_index
                    return True
            return False

        for truth_index in range(len(truth)):
            assign(truth_index, set())
        return tuple(sorted((truth_index, detection_index) for detection_index, truth_index in detection_owner.items()))

    @staticmethod
    def _validate_frame_order(frames: tuple[EvaluationFrame, ...]) -> None:
        previous_sequences: dict[tuple[str, str, str], int] = {}
        for item in frames:
            frame = item.result.frame
            identity = (frame.device_id, frame.camera_id, frame.stream_session_id)
            previous = previous_sequences.get(identity)
            if previous is not None and frame.frame_sequence <= previous:
                raise GroundTruthValidationError("evaluation frames must have strictly increasing frame sequences per stream")
            previous_sequences[identity] = frame.frame_sequence


def iou(left: BoundingBox, right: BoundingBox) -> float:
    """Return intersection-over-union for valid pixel boxes."""
    if not isinstance(left, BoundingBox) or not isinstance(right, BoundingBox):
        raise GroundTruthValidationError("IoU requires BoundingBox contracts")
    x1 = max(left.x_px, right.x_px)
    y1 = max(left.y_px, right.y_px)
    x2 = min(left.x_px + left.width_px, right.x_px + right.width_px)
    y2 = min(left.y_px + left.height_px, right.y_px + right.height_px)
    intersection = max(0, x2 - x1) * max(0, y2 - y1)
    union = left.width_px * left.height_px + right.width_px * right.height_px - intersection
    return intersection / union
