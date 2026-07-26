"""Coverage for the fail-closed ToolPolicy engine and the forbidden-namespace guard.

A grant happens only when every check passes; anything else is a recorded denial.
The engine and the registry share one denylist, so a flight-control-shaped
capability can neither be registered nor granted.
"""

import unittest

from brain.plugin_sdk import (
    PluginRegistry,
    PluginRegistryError,
    build_tool_policy,
    load_plugin_manifest,
    load_tool_policy,
)


def _manifest(plugin_id, *, provides=None, requests=None, version="1.0.0"):
    document = {
        "contract_version": "v0.1",
        "plugin_id": plugin_id,
        "version": version,
        "name": plugin_id,
        "provides": [
            {"capability_id": cap, "version": ver, "access": "read"}
            for cap, ver in (provides or [(f"{plugin_id}.read", "v0.1")])
        ],
    }
    if requests is not None:
        document["requests"] = [
            {"capability_id": cap, "version": ver} for cap, ver in requests
        ]
    return load_plugin_manifest(document)


def _policy_doc(policy):
    document = {
        "contract_version": "v0.1",
        "plugin_id": policy.plugin_id,
        "granted": [dict(item) for item in policy.granted],
        "denied": [dict(item) for item in policy.denied],
    }
    if policy.limits is not None:
        document["limits"] = policy.limits
    return document


class ToolPolicyTests(unittest.TestCase):
    def _registry_with_provider(self):
        registry = PluginRegistry()
        registry.register(_manifest("world_memory", provides=[("world_memory.query", "v0.1")]))
        return registry

    def test_grants_an_allowlisted_available_capability(self) -> None:
        registry = self._registry_with_provider()
        consumer = _manifest("telemetry", requests=[("world_memory.query", "v0.1")])
        policy = build_tool_policy(
            consumer, registry, allowlist={"world_memory.query"}, limits={"timeout_ms": 2000}
        )
        self.assertEqual(policy.granted, ({"capability_id": "world_memory.query", "version": "v0.1"},))
        self.assertEqual(policy.denied, ())
        # The produced policy must itself satisfy the versioned schema.
        load_tool_policy(_policy_doc(policy))

    def test_denies_capability_not_on_allowlist(self) -> None:
        registry = self._registry_with_provider()
        consumer = _manifest("telemetry", requests=[("world_memory.query", "v0.1")])
        policy = build_tool_policy(consumer, registry, allowlist=set())
        self.assertEqual(policy.granted, ())
        self.assertEqual(policy.denied[0]["reason"], "not on allowlist")

    def test_denies_when_no_provider_is_registered(self) -> None:
        registry = PluginRegistry()
        consumer = _manifest("telemetry", requests=[("world_memory.query", "v0.1")])
        policy = build_tool_policy(consumer, registry, allowlist={"world_memory.query"})
        self.assertEqual(policy.denied[0]["reason"], "no registered provider")

    def test_denies_on_version_mismatch(self) -> None:
        registry = self._registry_with_provider()
        consumer = _manifest("telemetry", requests=[("world_memory.query", "v0.2")])
        policy = build_tool_policy(consumer, registry, allowlist={"world_memory.query"})
        self.assertEqual(policy.denied[0]["reason"], "version v0.2 unavailable")

    def test_forbidden_namespace_is_denied_even_if_allowlisted(self) -> None:
        registry = PluginRegistry()
        consumer = _manifest("telemetry", requests=[("flight.arm", "v0.1")])
        policy = build_tool_policy(consumer, registry, allowlist={"flight.arm"})
        self.assertEqual(policy.granted, ())
        self.assertEqual(policy.denied[0]["reason"], "forbidden namespace")


class ForbiddenProvidesTests(unittest.TestCase):
    def test_registering_a_flight_control_capability_is_rejected(self) -> None:
        registry = PluginRegistry()
        with self.assertRaises(PluginRegistryError):
            registry.register(_manifest("rogue", provides=[("flight.arm", "v0.1")]))

    def test_forbidden_namespaces_cover_mavsdk_and_actuators(self) -> None:
        registry = PluginRegistry()
        for bad in ("mavsdk.send", "px4.command", "motor.spin", "offboard.setpoint"):
            with self.subTest(capability=bad):
                with self.assertRaises(PluginRegistryError):
                    registry.register(
                        _manifest(bad.replace(".", "_"), provides=[(bad, "v0.1")])
                    )


if __name__ == "__main__":
    unittest.main()
