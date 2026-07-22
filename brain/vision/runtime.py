"""Dependency-injected, observation-only P0 Vision Core runtime.

This module deliberately owns no camera, ML, simulator, or flight-control SDK.
Those concerns are supplied through narrow ports so a failed dependency is an
explicit health failure rather than a silently empty detection result.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Protocol

from .contracts import (
    DETECTION_RESULT_V1,
    CameraFrame,
    Detection,
    DetectionResult,
    FrameSequenceLedger,
    ResultState,
    VisionHealth,
)


class IngestPort(Protocol):
    """A non-blocking source of already-decoded, contract-boundary frames."""

    def poll(self) -> CameraFrame | None: ...


class DetectorPort(Protocol):
    """An observation-only detector. Implementations must not issue commands."""

    model_id: str
    model_version: str

    def detect(self, frame: CameraFrame, produced_at: datetime) -> tuple[Detection, ...]: ...


class TrackerPort(Protocol):
    """Optional tracker which only annotates detections with opaque track IDs."""

    def track(self, detections: tuple[Detection, ...], frame: CameraFrame) -> tuple[Detection, ...]: ...


class RuntimeState(str, Enum):
    IDLE = "idle"
    PROCESSED = "processed"
    REJECTED = "rejected"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class RuntimeOutcome:
    state: RuntimeState
    frame: CameraFrame | None
    validation: object | None
    detection: DetectionResult | None
    health: VisionHealth
    reason: str = ""


class CallbackIngestAdapter:
    """Adapter boundary for sources such as recorded video, Gazebo and GStreamer."""

    def __init__(self, poll_frame: Callable[[], CameraFrame | None]) -> None:
        self._poll_frame = poll_frame

    def poll(self) -> CameraFrame | None:
        return self._poll_frame()


class RecordedIngestAdapter(CallbackIngestAdapter):
    pass


class GazeboIngestAdapter(CallbackIngestAdapter):
    pass


class GStreamerIngestAdapter(CallbackIngestAdapter):
    pass


class DeterministicDetector:
    """Test/benchmark detector that returns fixed immutable observations."""

    model_id = "deterministic-stub"
    model_version = "v1"

    def __init__(self, detections: tuple[Detection, ...] = ()) -> None:
        self._detections = detections

    def detect(self, frame: CameraFrame, produced_at: datetime) -> tuple[Detection, ...]:
        return self._detections


class ModelDetectorAdapter:
    """Wrap an injected inference callable; model weights stay outside this domain."""

    def __init__(self, model_id: str, model_version: str, infer: Callable[[CameraFrame], Iterable[Detection]]) -> None:
        self.model_id = model_id
        self.model_version = model_version
        self._infer = infer

    def detect(self, frame: CameraFrame, produced_at: datetime) -> tuple[Detection, ...]:
        return tuple(self._infer(frame))


class YoloDetectorAdapter(ModelDetectorAdapter):
    pass


class RTDetrDetectorAdapter(ModelDetectorAdapter):
    pass


class BotSortTrackerAdapter:
    """Protocol adapter for externally supplied BoT-SORT assignment logic."""

    def __init__(self, assign: Callable[[tuple[Detection, ...], CameraFrame], Iterable[Detection]]) -> None:
        self._assign = assign

    def track(self, detections: tuple[Detection, ...], frame: CameraFrame) -> tuple[Detection, ...]:
        return tuple(self._assign(detections, frame))


class VisionRuntime:
    """Single-slot newest-frame-wins orchestrator with fail-closed health."""

    def __init__(self, detector: DetectorPort, tracker: TrackerPort | None = None) -> None:
        self._detector = detector
        self._tracker = tracker
        self._ledger = FrameSequenceLedger()
        self._pending: CameraFrame | None = None
        self._dropped_frames = 0
        self._stream_state = "healthy"
        self._model_state = "healthy"
        # P0 has no GPU telemetry adapter yet.  This is explicitly degraded,
        # not silently reported healthy; the pipeline remains usable on CPU.
        self._gpu_state = "degraded"
        self._last_reason = ""

    def submit(self, frame: CameraFrame) -> None:
        """Put a frame in the single-slot queue, dropping any older queued frame."""
        if self._pending is not None:
            self._dropped_frames += 1
        self._pending = frame

    def ingest_once(self, source: IngestPort) -> bool:
        """Poll a source once; adapter failures surface as unavailable health."""
        try:
            frame = source.poll()
        except Exception as error:  # source boundary: never crash the runtime loop
            self.disconnect(f"ingest error: {error}", datetime.now(UTC))
            return False
        if frame is None:
            return False
        self.submit(frame)
        return True

    def disconnect(self, reason: str, observed_at: datetime) -> None:
        self._stream_state = "unavailable"
        self._last_reason = reason

    def reconnect(self, observed_at: datetime) -> None:
        self._stream_state = "healthy"
        self._last_reason = ""

    def health(self, now: datetime) -> VisionHealth:
        return VisionHealth(
            observed_at=now,
            stream_state=self._stream_state,
            model_state=self._model_state,
            gpu_state=self._gpu_state,
            backlog_frames=1 if self._pending is not None else 0,
            dropped_frames=self._dropped_frames,
        )

    def process_next(self, now: datetime) -> RuntimeOutcome:
        frame = self._pending
        self._pending = None
        if self._stream_state != "healthy":
            return RuntimeOutcome(RuntimeState.UNAVAILABLE, frame, None, None, self.health(now), self._last_reason)
        if frame is None:
            return RuntimeOutcome(RuntimeState.IDLE, None, None, None, self.health(now))

        validation, self._ledger = self._ledger.validate(frame, now=now)
        if validation.state is not ResultState.VALID:
            return RuntimeOutcome(RuntimeState.REJECTED, frame, validation, None, self.health(now), validation.reason)
        try:
            detections = self._detector.detect(frame, now)
            if self._tracker is not None:
                detections = self._tracker.track(detections, frame)
            result = DetectionResult(
                contract_version=DETECTION_RESULT_V1,
                frame=frame,
                model_id=self._detector.model_id,
                model_version=self._detector.model_version,
                produced_at=now,
                detections=detections,
            )
        except Exception as error:
            self._model_state = "unavailable"
            self._last_reason = f"model error: {error}"
            return RuntimeOutcome(RuntimeState.UNAVAILABLE, frame, validation, None, self.health(now), self._last_reason)
        self._model_state = "healthy"
        return RuntimeOutcome(RuntimeState.PROCESSED, frame, validation, result, self.health(now))
