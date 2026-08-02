"""Physical fleet records are durable evidence, not mission configuration."""

from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4

from brain.fleet.registry import FleetRegistryError, load_registry, record_usb_bench_observation


def _bench_artifact(uid: str = "2c0038000b51323232393933") -> dict[str, object]:
    return {
        "version": "usb_bench_diagnostics.v1",
        "outcome": "completed",
        "captured_at": "2026-07-28T14:06:28Z",
        "px4_identity": {
            "status": "observed", "hardware_uid": uid, "legacy_uid": "123456789",
            "product": {"vendor_name": "Holybro", "product_name": "Pixhawk 6C"},
            "firmware": {"version": "1.17.0", "git_hash": "a1b2c3d4", "release_type": "RELEASE"},
        },
    }


class FleetRegistryTests(unittest.TestCase):
    def test_records_a_physical_uav_with_hash_bound_bench_evidence(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            artifact = artifacts / "bench.json"
            artifact.write_text(json.dumps(_bench_artifact()), encoding="utf-8")
            registry_path = root / "fleet.json"
            platform_id = str(uuid4())

            registry = record_usb_bench_observation(
                registry_path, artifact, artifact_root=artifacts, platform_id=platform_id,
                display_name="X500 physical 01", kind="aerial_multirotor", twin_id="x500v2_reference_01",
            )
            restored = load_registry(registry_path)

        self.assertEqual(registry.robots[0].platform_id, platform_id)
        self.assertEqual(restored.robots[0].hardware_uid, "2c0038000b51323232393933")
        self.assertEqual(restored.robots[0].observations[0].artifact, "bench.json")
        self.assertEqual(len(restored.robots[0].observations[0].evidence_sha256), 64)

    def test_refuses_uid_collision_and_artifact_outside_the_allowed_root(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            artifact = artifacts / "bench.json"
            artifact.write_text(json.dumps(_bench_artifact()), encoding="utf-8")
            registry_path = root / "fleet.json"
            record_usb_bench_observation(registry_path, artifact, artifact_root=artifacts, platform_id=str(uuid4()), display_name="one", kind="aerial_multirotor")
            with self.assertRaisesRegex(FleetRegistryError, "already belongs"):
                record_usb_bench_observation(registry_path, artifact, artifact_root=artifacts, platform_id=str(uuid4()), display_name="two", kind="ground_robot")
            outside = root / "outside.json"
            outside.write_text(json.dumps(_bench_artifact()), encoding="utf-8")
            with self.assertRaisesRegex(FleetRegistryError, "inside"):
                record_usb_bench_observation(registry_path, outside, artifact_root=artifacts, platform_id=str(uuid4()), display_name="outside", kind="ground_robot")

    def test_all_zero_hardware_uid_is_not_an_identity(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            artifact = root / "bench.json"
            artifact.write_text(json.dumps(_bench_artifact("0" * 32)), encoding="utf-8")
            with self.assertRaisesRegex(FleetRegistryError, "non-zero"):
                record_usb_bench_observation(root / "fleet.json", artifact, artifact_root=root, platform_id=str(uuid4()), display_name="x", kind="aerial_multirotor")


if __name__ == "__main__":
    unittest.main()
