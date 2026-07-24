"""The first reference plugin: a read-only telemetry reader on the Plugin SDK.

It proves the contract end to end -- a manifest, the register/start/health/stop
lifecycle, and a capability invoked through the registry -- against a real,
read-only data source: the dashboard telemetry snapshot. It exposes exactly one
capability, ``telemetry.read`` (access ``read``), and has no MAVSDK, MAVLink,
PX4 or actuator path. It is the pattern the workstream-D read-only plugins
follow.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from apps.dashboard.telemetry import (
    TelemetryFormatError,
    load_telemetry_snapshot,
)
from brain.plugin_sdk import PluginManifest, PluginRegistry, load_plugin_manifest


TELEMETRY_READ_MANIFEST = {
    "contract_version": "v0.1",
    "plugin_id": "telemetry.read",
    "version": "0.1.0",
    "name": "Telemetry Reader",
    "description": "Reads the dashboard telemetry snapshot as a read-only capability.",
    "provides": [
        {
            "capability_id": "telemetry.read",
            "version": "v0.1",
            "access": "read",
            "description": "Current vehicle telemetry snapshot from the local artifact.",
        }
    ],
}


class TelemetryReadPlugin:
    """A read-only telemetry plugin. Its only capability returns a snapshot dict."""

    def __init__(
        self,
        telemetry_path: Path,
        loader: Callable[[Path], Any] = load_telemetry_snapshot,
    ) -> None:
        self._path = Path(telemetry_path)
        self._loader = loader
        self._started = False

    # -- lifecycle hooks --------------------------------------------------

    def start(self) -> None:
        self._started = True

    def stop(self) -> None:
        self._started = False

    def health(self) -> str:
        """Healthy only when a telemetry snapshot can actually be read."""
        if not self._started:
            return "unknown"
        try:
            self._loader(self._path)
        except (TelemetryFormatError, OSError):
            return "unhealthy"
        return "ok"

    # -- capabilities -----------------------------------------------------

    def capabilities(self) -> dict[str, Callable[..., Any]]:
        return {"telemetry.read": self._read}

    def _read(self) -> dict[str, Any]:
        """Return the current telemetry snapshot as a plain dict."""
        return self._loader(self._path).as_dict()


def manifest() -> PluginManifest:
    """The plugin's validated manifest."""
    return load_plugin_manifest(TELEMETRY_READ_MANIFEST)


def register(registry: PluginRegistry, telemetry_path: Path) -> TelemetryReadPlugin:
    """Register a telemetry reader against a registry and return the instance."""
    instance = TelemetryReadPlugin(telemetry_path)
    registry.register(manifest(), instance)
    return instance
