"""Coverage for the versioned Plugin SDK v0.1 contracts.

Like the observation contract, most of these tests are about refusal: a
flight-control access class that cannot be expressed, a future contract version,
a manifest that provides nothing, a health snapshot that claims a failed plugin
is fine. The contract's whole job is to reject those before any registry runs.
"""

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import unittest

import jsonschema

from brain.plugin_sdk import (
    ALLOWED_ACCESS_CLASSES,
    PLUGIN_SDK_CONTRACT_VERSION,
    PluginContractError,
    load_capability,
    load_plugin_health,
    load_plugin_manifest,
    load_tool_policy,
)
from brain.plugin_sdk.contracts import CAPABILITY_SCHEMA_PATH, _validator


ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "shared/interfaces/plugin_sdk/examples"
PLUGIN_SDK_PKG = ROOT / "brain/plugin_sdk"

# Map a fixture's filename prefix to the loader that owns it.
_LOADERS = {
    "manifest_": load_plugin_manifest,
    "capability_": load_capability,
    "tool_policy_": load_tool_policy,
    "health_": load_plugin_health,
}


def _loader_for(path: Path):
    for prefix, loader in _LOADERS.items():
        if path.name.startswith(prefix):
            return loader
    raise AssertionError(f"fixture '{path.name}' has no known contract prefix")


def _read(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))


class FixtureTests(unittest.TestCase):
    def test_every_valid_fixture_is_accepted(self) -> None:
        fixtures = sorted((EXAMPLES / "valid").glob("*.json"))
        self.assertTrue(fixtures, "the contract needs valid fixtures to be worth anything")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                _loader_for(path)(_read(path))

    def test_every_invalid_fixture_is_rejected(self) -> None:
        fixtures = sorted((EXAMPLES / "invalid").glob("*.json"))
        self.assertTrue(fixtures, "refusal is the point; there must be invalid fixtures")
        for path in fixtures:
            with self.subTest(fixture=path.name):
                with self.assertRaises(PluginContractError):
                    _loader_for(path)(_read(path))


class SafetyBoundaryTests(unittest.TestCase):
    def test_access_enum_has_no_actuation_class(self) -> None:
        # The core safety invariant: a flight-control capability cannot be named.
        self.assertNotIn("actuate", ALLOWED_ACCESS_CLASSES)
        self.assertNotIn("control", ALLOWED_ACCESS_CLASSES)
        self.assertEqual(
            ALLOWED_ACCESS_CLASSES,
            frozenset({"read", "query", "propose", "communicate"}),
        )

    def test_actuate_capability_is_rejected_by_schema(self) -> None:
        with self.assertRaises(PluginContractError):
            load_capability(
                {"capability_id": "flight.arm", "version": "v0.1", "access": "actuate"}
            )

    def test_plugin_sdk_never_imports_flight_control(self) -> None:
        forbidden = ("mavsdk", "mavlink", "px4", "pymavlink")
        for source in PLUGIN_SDK_PKG.rglob("*.py"):
            text = source.read_text(encoding="utf-8").lower()
            for needle in forbidden:
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(
                        f"import {needle}", text, f"{source.name} must not import {needle}"
                    )
                    self.assertNotIn(
                        f"from {needle}", text, f"{source.name} must not import from {needle}"
                    )


class CompatibilityTests(unittest.TestCase):
    def test_future_contract_version_is_rejected(self) -> None:
        with self.assertRaises(PluginContractError):
            load_plugin_manifest(
                {
                    "contract_version": "v0.2",
                    "plugin_id": "telemetry.read",
                    "version": "1.0.0",
                    "name": "Telemetry Reader",
                    "provides": [
                        {"capability_id": "telemetry.read", "version": "v0.1", "access": "read"}
                    ],
                }
            )

    def test_embedded_and_standalone_capability_agree(self) -> None:
        # The manifest inlines a capability shape; assert it cannot drift from the
        # standalone capability schema by validating every provided capability
        # from valid manifests against that schema too.
        capability_validator = _validator(CAPABILITY_SCHEMA_PATH)
        manifests = sorted((EXAMPLES / "valid").glob("manifest_*.json"))
        self.assertTrue(manifests)
        checked = 0
        for path in manifests:
            manifest = load_plugin_manifest(_read(path))
            for capability in manifest.provides:
                document = {
                    "capability_id": capability.capability_id,
                    "version": capability.version,
                    "access": capability.access,
                }
                if capability.data_contract is not None:
                    document["data_contract"] = capability.data_contract
                if capability.description is not None:
                    document["description"] = capability.description
                capability_validator.validate(document)
                checked += 1
        self.assertGreater(checked, 0, "valid manifests must provide capabilities to check")


class HealthFreshnessTests(unittest.TestCase):
    def _health(self, max_age_s: float = 10.0):
        return load_plugin_health(
            {
                "contract_version": PLUGIN_SDK_CONTRACT_VERSION,
                "plugin_id": "telemetry.read",
                "lifecycle_state": "started",
                "health": "ok",
                "checked_at": "2026-07-22T09:00:00+00:00",
                "max_age_s": max_age_s,
            }
        )

    def test_snapshot_within_window_is_fresh(self) -> None:
        health = self._health(max_age_s=10.0)
        now = health.checked_at + timedelta(seconds=5)
        self.assertTrue(health.is_fresh(now))

    def test_snapshot_past_its_window_is_stale(self) -> None:
        health = self._health(max_age_s=10.0)
        now = health.checked_at + timedelta(seconds=30)
        self.assertFalse(health.is_fresh(now))

    def test_naive_now_is_refused(self) -> None:
        health = self._health()
        with self.assertRaises(PluginContractError):
            health.age_s(datetime(2026, 7, 22, 9, 0, 0))  # no tzinfo

    def test_space_separated_timestamp_is_refused(self) -> None:
        # The schema's date-time format requires a 'T'; a space-joined timestamp
        # that fromisoformat would otherwise accept must be rejected.
        with self.assertRaises(PluginContractError):
            load_plugin_health(
                {
                    "contract_version": PLUGIN_SDK_CONTRACT_VERSION,
                    "plugin_id": "telemetry.read",
                    "lifecycle_state": "started",
                    "health": "ok",
                    "checked_at": "2026-07-22 09:00:00+00:00",
                    "max_age_s": 10,
                }
            )


if __name__ == "__main__":
    unittest.main()
