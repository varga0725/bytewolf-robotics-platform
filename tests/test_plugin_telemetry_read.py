"""End-to-end coverage for the first reference plugin over the Plugin SDK.

Proves the whole contract with a real read-only source: register the telemetry
plugin, start it, take a health snapshot, and invoke its capability through the
registry -- including that a ToolPolicy is the enforcement point for a consumer,
and that an unstarted provider cannot be invoked.
"""

import json
from pathlib import Path
import tempfile
import unittest

from apps.plugins.telemetry_read import TELEMETRY_READ_MANIFEST, manifest, register
from brain.plugin_sdk import (
    LifecycleState,
    PluginRegistry,
    PluginRegistryError,
    build_tool_policy,
    load_plugin_manifest,
)


_SNAPSHOT = {
    "telemetry": {
        "position": {
            "latitude_deg": 47.5,
            "longitude_deg": 19.05,
            "absolute_altitude_m": 120.0,
            "relative_altitude_m": 5.0,
        },
        "battery": {"remaining_percent": 87.5},
        "in_air": True,
        "captured_at": "2026-07-22T09:00:00Z",
    }
}


class TelemetryReadPluginTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.path = Path(self._dir.name) / "live-telemetry.json"
        self.path.write_text(json.dumps(_SNAPSHOT), encoding="utf-8")
        self.addCleanup(self._dir.cleanup)

    def test_manifest_provides_only_a_read_capability(self) -> None:
        provided = manifest().provides
        self.assertEqual(len(provided), 1)
        self.assertEqual(provided[0].capability_id, "telemetry.read")
        self.assertEqual(provided[0].access, "read")

    def test_lifecycle_and_direct_invocation(self) -> None:
        registry = PluginRegistry()
        register(registry, self.path)
        registry.start("telemetry.read")
        self.assertEqual(registry.state("telemetry.read"), LifecycleState.STARTED)

        snapshot = registry.invoke("telemetry.read")
        self.assertEqual(snapshot["battery_percent"], 87.5)
        self.assertTrue(snapshot["in_air"])

        health = registry.health("telemetry.read")
        self.assertEqual(health.health, "ok")

    def test_consumer_needs_a_grant_to_invoke(self) -> None:
        registry = PluginRegistry()
        register(registry, self.path)
        registry.start("telemetry.read")

        consumer = load_plugin_manifest(
            {
                "contract_version": "v0.1",
                "plugin_id": "agent.runtime",
                "version": "0.1.0",
                "name": "Agent",
                "provides": [
                    {"capability_id": "agent.runtime.status", "version": "v0.1", "access": "read"}
                ],
                "requests": [{"capability_id": "telemetry.read", "version": "v0.1"}],
            }
        )
        granting = build_tool_policy(consumer, registry, allowlist={"telemetry.read"})
        self.assertEqual(registry.invoke("telemetry.read", policy=granting)["battery_percent"], 87.5)

        ungranting = build_tool_policy(consumer, registry, allowlist=set())
        with self.assertRaises(PluginRegistryError):
            registry.invoke("telemetry.read", policy=ungranting)

    def test_invocation_before_start_is_refused(self) -> None:
        registry = PluginRegistry()
        register(registry, self.path)
        with self.assertRaises(PluginRegistryError):
            registry.invoke("telemetry.read")

    def test_health_is_unhealthy_when_the_artifact_is_unreadable(self) -> None:
        registry = PluginRegistry()
        register(registry, Path(self._dir.name) / "does-not-exist.json")
        registry.start("telemetry.read")
        self.assertEqual(registry.health("telemetry.read").health, "unhealthy")

    def test_manifest_constant_is_schema_valid(self) -> None:
        # The module constant must stay a valid manifest, not just the loaded copy.
        load_plugin_manifest(TELEMETRY_READ_MANIFEST)


if __name__ == "__main__":
    unittest.main()
