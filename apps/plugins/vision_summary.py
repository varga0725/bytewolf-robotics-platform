"""Read-only vision summary plugin: the get_vision_summary tool as a capability.

It reads the current canonical VisionSummary artifacts (the shared
vision_summary v0.1 contract) -- one per camera feed -- through the fail-closed
loader, and returns a bounded, self-qualifying summary: per source whether the
reading is valid and fresh and how many detections it holds, plus a bounded list
of detection identities (label and confidence) so the agent can say *what* the
camera sees, not merely how many. It never controls the drone, never invents a
detection, and fails soft: a missing or contract-violating artifact reports
unavailable for that source rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from brain.plugin_sdk import PluginManifest, PluginRegistry, load_plugin_manifest
from brain.vision.canonical import VisionContractError, VisionState, load_vision_summary


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
        now = self._now()
        for name, path in self._sources.items():
            summary = self._read(path)
            if summary is None:
                per_source[name] = {"available": False}
                continue
            fresh = summary.state(now) is VisionState.VALID
            any_fresh = any_fresh or fresh
            total += summary.detection_count
            for detection in summary.detections:
                if len(identities) < MAX_DETECTION_IDENTITIES:
                    identities.append({
                        "source": name,
                        "label": detection.get("label"),
                        "confidence": detection.get("confidence"),
                    })
            per_source[name] = {
                "available": True,
                "validity": summary.declared_validity,
                "observed_at": summary.observed_at.isoformat(),
                "fresh": fresh,
                "detection_count": summary.detection_count,
            }
        return {
            "available": any(source.get("available") for source in per_source.values()),
            "fresh": any_fresh,
            "detection_count": total,
            "detections": identities,
            "sources": per_source,
        }

    def _read(self, path: Path):
        """Read one source as a canonical VisionSummary, or None if unavailable.

        A missing file, unreadable JSON, or a document that does not satisfy the
        shared vision_summary v0.1 contract is reported as unavailable for that
        source -- never an exception into the turn.
        """
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        try:
            return load_vision_summary(document)
        except VisionContractError:
            return None


def manifest() -> PluginManifest:
    return load_plugin_manifest(VISION_SUMMARY_MANIFEST)


def register(registry: PluginRegistry, sources: Mapping[str, Path] | Path | str) -> VisionSummaryPlugin:
    instance = VisionSummaryPlugin(sources)
    registry.register(manifest(), instance)
    return instance
