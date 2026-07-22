"""Coverage for the Plugin SDK registry and lifecycle.

The registry's contract is to refuse anything that would leave the plugin set
inconsistent, so most of these tests assert a rejection: a duplicate id, a second
provider of a capability, a declared conflict, a missing or too-old dependency, a
cycle, stopping something still depended on, reloading a non-reloadable plugin.
"""

from datetime import UTC, datetime
import unittest

from brain.plugin_sdk import (
    LifecycleState,
    PluginRegistry,
    PluginRegistryError,
    load_plugin_manifest,
    version_satisfies,
)


def _manifest(
    plugin_id: str,
    *,
    version: str = "1.0.0",
    provides: list[str] | None = None,
    requires: list[dict] | None = None,
    conflicts: list[str] | None = None,
    reloadable: bool = False,
):
    caps = provides if provides is not None else [f"{plugin_id}.read"]
    document = {
        "contract_version": "v0.1",
        "plugin_id": plugin_id,
        "version": version,
        "name": plugin_id,
        "provides": [
            {"capability_id": cap, "version": "v0.1", "access": "read"} for cap in caps
        ],
    }
    if requires is not None:
        document["requires"] = requires
    if conflicts is not None:
        document["conflicts"] = conflicts
    if reloadable:
        document["reloadable"] = True
    return load_plugin_manifest(document)


class _Recorder:
    """A plugin instance that records which lifecycle hooks fired."""

    def __init__(self, *, health: str = "ok") -> None:
        self.calls: list[str] = []
        self._health = health

    def start(self) -> None:
        self.calls.append("start")

    def stop(self) -> None:
        self.calls.append("stop")

    def reload(self) -> None:
        self.calls.append("reload")

    def health(self) -> str:
        return self._health


class RegistrationTests(unittest.TestCase):
    def test_duplicate_plugin_id_is_rejected(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("telemetry.read"))
        with self.assertRaises(PluginRegistryError):
            registry.register(_manifest("telemetry.read"))

    def test_second_provider_of_a_capability_is_rejected(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("first", provides=["shared.cap"]))
        with self.assertRaises(PluginRegistryError):
            registry.register(_manifest("second", provides=["shared.cap"]))

    def test_declared_conflict_is_rejected_both_ways(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("a"))
        with self.assertRaises(PluginRegistryError):
            registry.register(_manifest("b", conflicts=["a"]))

        other = PluginRegistry()
        other.register(_manifest("a", conflicts=["b"]))
        with self.assertRaises(PluginRegistryError):
            other.register(_manifest("b"))

    def test_circular_dependency_is_rejected_and_rolled_back(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("a", requires=[{"plugin_id": "b", "version_range": ">=1.0.0"}]))
        with self.assertRaises(PluginRegistryError):
            registry.register(
                _manifest("b", requires=[{"plugin_id": "a", "version_range": ">=1.0.0"}])
            )
        # The rejected plugin must not linger in the set.
        self.assertEqual(registry.plugin_ids(), ("a",))


class LifecycleTests(unittest.TestCase):
    def test_start_brings_up_dependencies_first(self) -> None:
        registry = PluginRegistry()
        dep = _Recorder()
        app = _Recorder()
        registry.register(_manifest("dep"), dep)
        registry.register(
            _manifest("app", requires=[{"plugin_id": "dep", "version_range": ">=1.0.0"}]), app
        )
        registry.start("app")
        self.assertEqual(registry.state("dep"), LifecycleState.STARTED)
        self.assertEqual(registry.state("app"), LifecycleState.STARTED)
        self.assertEqual(dep.calls, ["start"])

    def test_start_with_missing_dependency_is_refused(self) -> None:
        registry = PluginRegistry()
        registry.register(
            _manifest("app", requires=[{"plugin_id": "dep", "version_range": ">=1.0.0"}])
        )
        with self.assertRaises(PluginRegistryError):
            registry.start("app")
        self.assertEqual(registry.state("app"), LifecycleState.REGISTERED)

    def test_start_with_incompatible_dependency_version_is_refused(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("dep", version="1.0.0"))
        registry.register(
            _manifest("app", requires=[{"plugin_id": "dep", "version_range": ">=2.0.0"}])
        )
        with self.assertRaises(PluginRegistryError):
            registry.start("app")

    def test_stop_is_refused_while_a_started_plugin_depends_on_it(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("dep"))
        registry.register(
            _manifest("app", requires=[{"plugin_id": "dep", "version_range": ">=1.0.0"}])
        )
        registry.start("app")
        with self.assertRaises(PluginRegistryError):
            registry.stop("dep")
        # Stopping the dependent first frees the dependency.
        registry.stop("app")
        registry.stop("dep")
        self.assertEqual(registry.state("dep"), LifecycleState.STOPPED)

    def test_failing_start_hook_marks_failed_and_does_not_start(self) -> None:
        class Boom:
            def start(self) -> None:
                raise RuntimeError("no")

        registry = PluginRegistry()
        registry.register(_manifest("boom"), Boom())
        with self.assertRaises(PluginRegistryError):
            registry.start("boom")
        self.assertEqual(registry.state("boom"), LifecycleState.FAILED)


class ReloadTests(unittest.TestCase):
    def test_reload_requires_reloadable_manifest(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("static"), _Recorder())
        registry.start("static")
        with self.assertRaises(PluginRegistryError):
            registry.reload("static")

    def test_reload_calls_reload_hook_and_returns_to_started(self) -> None:
        registry = PluginRegistry()
        recorder = _Recorder()
        registry.register(_manifest("live", reloadable=True), recorder)
        registry.start("live")
        registry.reload("live")
        self.assertEqual(registry.state("live"), LifecycleState.STARTED)
        self.assertEqual(recorder.calls, ["start", "reload"])

    def test_reload_of_unstarted_plugin_is_refused(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("live", reloadable=True), _Recorder())
        with self.assertRaises(PluginRegistryError):
            registry.reload("live")


class HealthTests(unittest.TestCase):
    def test_started_plugin_reports_its_own_health(self) -> None:
        registry = PluginRegistry()
        registry.register(_manifest("t"), _Recorder(health="degraded"))
        registry.start("t")
        snapshot = registry.health("t", now=datetime(2026, 7, 22, 9, 0, tzinfo=UTC))
        self.assertEqual(snapshot.lifecycle_state, "started")
        self.assertEqual(snapshot.health, "degraded")

    def test_failed_plugin_is_never_ok(self) -> None:
        class Boom:
            def start(self) -> None:
                raise RuntimeError("no")

        registry = PluginRegistry()
        registry.register(_manifest("boom"), Boom())
        with self.assertRaises(PluginRegistryError):
            registry.start("boom")
        snapshot = registry.health("boom", now=datetime(2026, 7, 22, 9, 0, tzinfo=UTC))
        self.assertEqual(snapshot.lifecycle_state, "failed")
        self.assertNotEqual(snapshot.health, "ok")


class VersionRangeTests(unittest.TestCase):
    def test_satisfies_and_violations(self) -> None:
        self.assertTrue(version_satisfies("1.2.0", ">=1.0.0,<2.0.0"))
        self.assertFalse(version_satisfies("2.0.0", ">=1.0.0,<2.0.0"))
        self.assertTrue(version_satisfies("1.0.0", "==1.0.0"))
        self.assertFalse(version_satisfies("0.9.0", ">=1.0.0"))

    def test_unparseable_range_raises_rather_than_passing(self) -> None:
        with self.assertRaises(PluginRegistryError):
            version_satisfies("1.0.0", "~1.0.0")
        with self.assertRaises(PluginRegistryError):
            version_satisfies("1.0", ">=1.0.0")


if __name__ == "__main__":
    unittest.main()
