"""Read-only Vision dashboard artifacts.

The runtime remains independent of image libraries and HTTP.  An injected
renderer creates a display frame, while this module atomically publishes only
observation and health data for the existing GET-only dashboard.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
import os

from .contracts import DetectionResult, ResultState, VisionHealth
from .metadata import LocalVisionMetadataStore


def vision_status_document(
    result: DetectionResult | None,
    health: VisionHealth,
    *,
    now: datetime,
) -> dict[str, object]:
    """Return the bounded, read-only document consumed by the dashboard."""
    state = health.state(now)
    detections: list[dict[str, object]] = []
    if result is not None:
        result_state = result.state(now)
        if result_state is not ResultState.VALID:
            state = result_state
        # Dashboard evidence remains visible even when a separate health probe
        # is degraded.  The explicit top-level state prevents a UI consumer
        # from treating those boxes as fresh, actionable perception.
        detections = [
            {
                "label": item.label,
                "confidence": item.confidence,
                "tracker_id": item.tracker_id,
                "bounding_box": {
                    "x_px": item.bounding_box.x_px,
                    "y_px": item.bounding_box.y_px,
                    "width_px": item.bounding_box.width_px,
                    "height_px": item.bounding_box.height_px,
                },
            }
            for item in result.detections
        ]
    return {
        "contract_version": "vision_dashboard.v1",
        "state": state.value,
        "observed_at": now.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "track_count": sum(1 for item in detections if item["tracker_id"] is not None),
        "detections": detections,
        "backlog_frames": health.backlog_frames,
        "dropped_frames": health.dropped_frames,
        "stream_state": health.stream_state,
        "model_state": health.model_state,
        "gpu_state": health.gpu_state,
    }


class VisionArtifactPublisher:
    """Atomically publish the local read model; never accepts HTTP input."""

    def __init__(
        self, status_path: Path, frame_path: Path, metadata_path: Path | None = None,
    ) -> None:
        self._status_path = status_path
        self._frame_path = frame_path
        self._metadata_store = (
            LocalVisionMetadataStore(metadata_path) if metadata_path is not None else None
        )

    def publish(
        self,
        result: DetectionResult | None,
        health: VisionHealth,
        *,
        now: datetime,
        render: Callable[[DetectionResult | None], bytes],
    ) -> None:
        """Render and publish a frame plus status without exposing a control path."""
        document = vision_status_document(result, health, now=now)
        payload = render(result)
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("Vision renderer must return non-empty encoded image bytes.")
        _atomic_write(self._status_path, json.dumps(document, separators=(",", ":")).encode("utf-8"))
        _atomic_write(self._frame_path, payload)
        if self._metadata_store is not None:
            self._metadata_store.append_dashboard_status(document, written_at=now)


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(payload)
    os.replace(temporary, path)
