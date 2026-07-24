"""Read-only world-memory query plugin: the world briefing as a capability.

It resolves the evidence-backed world memory for the current instant -- the same
recall/disputed resolution the dashboard uses -- and returns it as the bounded,
self-qualifying briefing text. It reads only; it holds no second store, admits
nothing, and never controls the drone. An unreadable store reports an empty
briefing rather than raising.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from brain.memory.briefing import world_briefing
from brain.memory.world_memory import load_world_memory
from brain.plugin_sdk import PluginManifest, PluginRegistry, load_plugin_manifest


WORLD_QUERY_MANIFEST = {
    "contract_version": "v0.1",
    "plugin_id": "world_memory.query",
    "version": "0.1.0",
    "name": "World Memory Query",
    "description": "Answers a bounded question over the evidence-backed world store, read-only.",
    "provides": [
        {
            "capability_id": "world_memory.query",
            "version": "v0.1",
            "access": "query",
            "data_contract": {"name": "world_claim", "version": "v0.1"},
            "description": "The current, self-qualifying world briefing.",
        }
    ],
}


class WorldMemoryQueryPlugin:
    """Returns the current world briefing text, read-only and fail-soft."""

    def __init__(self, world_memory_path: Path, now: Callable[[], datetime] | None = None) -> None:
        self._path = Path(world_memory_path)
        self._now = now or (lambda: datetime.now(UTC))
        self._started = False

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> str:
        return "ok" if self._started else "unknown"

    def capabilities(self) -> dict[str, Callable[..., Any]]:
        return {"world_memory.query": self._query}

    def _query(self) -> dict[str, Any]:
        try:
            memory = load_world_memory(self._path)
            now = self._now()
            briefing = world_briefing(memory.recall(now), memory.disputed(now), now=now)
        except (OSError, ValueError):
            return {"briefing": ""}
        return {"briefing": briefing}


def manifest() -> PluginManifest:
    return load_plugin_manifest(WORLD_QUERY_MANIFEST)


def register(registry: PluginRegistry, world_memory_path: Path) -> WorldMemoryQueryPlugin:
    instance = WorldMemoryQueryPlugin(world_memory_path)
    registry.register(manifest(), instance)
    return instance
