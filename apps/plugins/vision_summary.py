"""Read-only vision summary plugin: the get_vision_summary tool as a capability.

It reads the current detection artifact (the versioned detection_v0_1 document)
and returns a bounded, self-qualifying summary: whether the reading is valid and
fresh, how many detections there are, and the frame and source. It never controls
the drone, never invents a detection, and fails soft -- an unreadable artifact
reports "no fresh detections", not an exception.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from brain.plugin_sdk import PluginManifest, PluginRegistry, load_plugin_manifest


VISION_SUMMARY_MANIFEST = {
    "contract_version": "v0.1",
    "plugin_id": "vision.summary",
    "version": "0.1.0",
    "name": "Vision Summary",
    "description": "Summarises the current camera detection evidence, read-only.",
    "provides": [
        {
            "capability_id": "vision.summary",
            "version": "v0.1",
            "access": "read",
            "data_contract": {"name": "detection", "version": "v0.1"},
            "description": "Current detection summary from the local vision artifact.",
        }
    ],
}


class VisionSummaryPlugin:
    """Reads the detection artifact and returns a bounded summary."""

    def __init__(self, detections_path: Path, now: Callable[[], datetime] | None = None) -> None:
        self._path = Path(detections_path)
        self._now = now or (lambda: datetime.now(UTC))
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> str:
        if not self._started:
            return "unknown"
        return "ok" if self._path.is_file() else "degraded"

    def capabilities(self) -> dict[str, Callable[..., Any]]:
        return {"vision.summary": self._summary}

    def _summary(self) -> dict[str, Any]:
        try:
            document = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"available": False, "fresh": False, "detection_count": 0}
        if not isinstance(document, dict):
            return {"available": False, "fresh": False, "detection_count": 0}
        detections = document.get("detections")
        detections = detections if isinstance(detections, list) else []
        return {
            "available": True,
            "validity": document.get("validity"),
            "captured_at": document.get("captured_at"),
            "fresh": self._is_fresh(document),
            "detection_count": len(detections),
            "frame": document.get("frame"),
            "source": document.get("source"),
        }

    def _is_fresh(self, document: dict[str, Any]) -> bool:
        if document.get("validity") != "valid":
            return False
        captured_at = document.get("captured_at")
        max_age_s = document.get("max_age_s")
        if not isinstance(captured_at, str) or not isinstance(max_age_s, (int, float)):
            return False
        try:
            observed = datetime.fromisoformat(captured_at.replace("Z", "+00:00"))
        except ValueError:
            return False
        if observed.tzinfo is None:
            return False
        age = (self._now() - observed.astimezone(UTC)).total_seconds()
        return 0 <= age <= float(max_age_s)


def manifest() -> PluginManifest:
    return load_plugin_manifest(VISION_SUMMARY_MANIFEST)


def register(registry: PluginRegistry, detections_path: Path) -> VisionSummaryPlugin:
    instance = VisionSummaryPlugin(detections_path)
    registry.register(manifest(), instance)
    return instance
