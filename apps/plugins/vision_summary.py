"""Read-only vision summary plugin: the get_vision_summary tool as a capability.

It reads the current detection artifacts (versioned detection_v0_1 documents) --
one per camera feed -- and returns a bounded, self-qualifying summary: per source
whether the reading is valid and fresh and how many detections it holds, plus a
bounded list of detection identities (label and confidence) so the agent can say
*what* the camera sees, not merely how many. It never controls the drone, never
invents a detection, and fails soft: an unreadable artifact reports unavailable
for that source rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from brain.plugin_sdk import PluginManifest, PluginRegistry, load_plugin_manifest


MAX_DETECTION_IDENTITIES = 8

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
            "description": "Current detection summary from the local vision artifacts.",
        }
    ],
}


class VisionSummaryPlugin:
    """Reads one or more detection artifacts and returns a bounded summary."""

    def __init__(self, sources: Mapping[str, Path] | Path | str, now: Callable[[], datetime] | None = None) -> None:
        self._sources: dict[str, Path] = (
            {"camera": Path(sources)} if isinstance(sources, (str, Path))
            else {name: Path(path) for name, path in sources.items()}
        )
        self._now = now or (lambda: datetime.now(UTC))
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> str:
        if not self._started:
            return "unknown"
        return "ok" if any(path.is_file() for path in self._sources.values()) else "degraded"

    def capabilities(self) -> dict[str, Callable[..., Any]]:
        return {"vision.summary": self._summary}

    def _summary(self) -> dict[str, Any]:
        per_source: dict[str, Any] = {}
        identities: list[dict[str, Any]] = []
        total = 0
        any_fresh = False
        for name, path in self._sources.items():
            document = self._read(path)
            if document is None:
                per_source[name] = {"available": False}
                continue
            detections = document.get("detections")
            detections = detections if isinstance(detections, list) else []
            fresh = self._is_fresh(document)
            any_fresh = any_fresh or fresh
            total += len(detections)
            for detection in detections:
                if isinstance(detection, dict) and len(identities) < MAX_DETECTION_IDENTITIES:
                    identities.append({
                        "source": name,
                        "label": detection.get("label"),
                        "confidence": detection.get("confidence"),
                    })
            per_source[name] = {
                "available": True,
                "validity": document.get("validity"),
                "captured_at": document.get("captured_at"),
                "fresh": fresh,
                "detection_count": len(detections),
            }
        return {
            "available": any(source.get("available") for source in per_source.values()),
            "fresh": any_fresh,
            "detection_count": total,
            "detections": identities,
            "sources": per_source,
        }

    def _read(self, path: Path) -> dict[str, Any] | None:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return document if isinstance(document, dict) else None

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


def register(registry: PluginRegistry, sources: Mapping[str, Path] | Path | str) -> VisionSummaryPlugin:
    instance = VisionSummaryPlugin(sources)
    registry.register(manifest(), instance)
    return instance
