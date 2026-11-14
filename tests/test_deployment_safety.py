"""Deployment policy must fail closed without touching PX4 or a simulator."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from uuid import uuid4

from brain.safety.deployment import (
    DeploymentAuthorizationError,
    DeploymentMode,
    actuation_request_sha256,
    authorize_actuation_connection,
    authorize_readonly_connection,
)


NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)
VEHICLE = "x500v2_reference_01"
ENDPOINT = "serial:///dev/ttyACM0:57600"
OPERATION = "takeoff-hover-land"
REQUEST_HASH = "1" * 64
PROFILE_HASH = "2" * 64


def _permit(**updates: object) -> dict[str, object]:
    document: dict[str, object] = {
        "schema_version": "physical_actuation_permit.v0.1",
        "permit_id": str(uuid4()),
        "vehicle_id": VEHICLE,
        "endpoint": ENDPOINT,
        "operation": OPERATION,
        "request_sha256": REQUEST_HASH,
        "safety_profile_sha256": PROFILE_HASH,
        "operator": "operator-a",
        "safety_observer": "observer-b",
        "approved_at": (NOW - timedelta(minutes=1)).isoformat(),
        "expires_at": (NOW + timedelta(minutes=4)).isoformat(),
        "operator_checklist_confirmed": True,
    }
    document.update(updates)
    return document


def _write_permit(directory: str, document: dict[str, object]) -> Path:
    path = Path(directory) / "permit.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    path.chmod(0o600)
    return path


def _authorize(path: Path | None, *, enabled: bool = True) -> DeploymentMode:
    return authorize_actuation_connection(
        DeploymentMode.PHYSICAL_ACTUATION,
        ENDPOINT,
        vehicle_id=VEHICLE,
        operation=OPERATION,
        request_sha256=REQUEST_HASH,
        safety_profile_sha256=PROFILE_HASH,
        physical_actuation_enabled=enabled,
        permit_path=path,
        now=NOW,
    )


class ReadOnlyDeploymentTests(unittest.TestCase):
    def test_simulation_accepts_loopback_udp_only(self) -> None:
        self.assertEqual(
            authorize_readonly_connection("simulation", "udpin://127.0.0.1:14540"),
            DeploymentMode.SIMULATION,
        )
        for endpoint in (
            "udpin://0.0.0.0:14540",
            "udpin://192.168.1.20:14540",
            "serial:///dev/ttyACM0:57600",
        ):
            with self.subTest(endpoint=endpoint), self.assertRaises(
                DeploymentAuthorizationError
            ):
                authorize_readonly_connection("simulation", endpoint)

    def test_bench_mode_accepts_direct_serial_only(self) -> None:
        self.assertEqual(
            authorize_readonly_connection("bench_readonly", ENDPOINT),
            DeploymentMode.BENCH_READONLY,
        )
        with self.assertRaisesRegex(DeploymentAuthorizationError, "direct serial"):
            authorize_readonly_connection("bench_readonly", "udpin://127.0.0.1:14540")

    def test_physical_telemetry_can_observe_but_never_actuate(self) -> None:
        self.assertEqual(
            authorize_readonly_connection("physical_telemetry", ENDPOINT),
            DeploymentMode.PHYSICAL_TELEMETRY,
        )
        with self.assertRaisesRegex(DeploymentAuthorizationError, "read-only"):
            authorize_actuation_connection(
                "physical_telemetry",
                ENDPOINT,
                vehicle_id=VEHICLE,
                operation=OPERATION,
                request_sha256=REQUEST_HASH,
                safety_profile_sha256=PROFILE_HASH,
                physical_actuation_enabled=True,
                permit_path=None,
                now=NOW,
            )

    def test_unknown_mode_and_endpoint_scheme_fail_closed(self) -> None:
        with self.assertRaisesRegex(DeploymentAuthorizationError, "Unknown deployment mode"):
            authorize_readonly_connection("automatic", ENDPOINT)
        with self.assertRaisesRegex(DeploymentAuthorizationError, "Unsupported"):
            authorize_readonly_connection("physical_telemetry", "file:///tmp/fake")


class PhysicalActuationPermitTests(unittest.TestCase):
    def test_software_release_lock_blocks_even_an_enabled_alternate_profile(self) -> None:
        with self.assertRaisesRegex(DeploymentAuthorizationError, "not released"):
            _authorize(Path("does-not-exist.json"), enabled=True)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_shipped_policy_blocks_physical_actuation_before_a_permit_matters(self) -> None:
        with self.assertRaisesRegex(DeploymentAuthorizationError, "disabled by the active vehicle twin"):
            _authorize(Path("does-not-exist.json"), enabled=False)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_enabled_policy_still_requires_a_permit(self) -> None:
        with self.assertRaisesRegex(DeploymentAuthorizationError, "requires --physical"):
            _authorize(None)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_valid_permit_is_single_use_and_consumed_before_connection(self) -> None:
        with TemporaryDirectory() as directory:
            path = _write_permit(directory, _permit())

            self.assertEqual(_authorize(path), DeploymentMode.PHYSICAL_ACTUATION)
            consumed = Path(f"{path}.consumed")
            self.assertTrue(consumed.is_file())
            with self.assertRaisesRegex(DeploymentAuthorizationError, "already been consumed"):
                _authorize(path)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_permit_is_bound_to_request_and_safety_profile_hashes(self) -> None:
        for field in ("request_sha256", "safety_profile_sha256"):
            with self.subTest(field=field), TemporaryDirectory() as directory:
                path = _write_permit(directory, _permit(**{field: "a" * 64}))
                with self.assertRaisesRegex(DeploymentAuthorizationError, field):
                    _authorize(path)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_expired_future_and_overlong_permits_are_refused(self) -> None:
        cases = {
            "expired": _permit(expires_at=(NOW - timedelta(seconds=1)).isoformat()),
            "not active": _permit(approved_at=(NOW + timedelta(seconds=1)).isoformat()),
            "15 minutes": _permit(
                approved_at=(NOW - timedelta(seconds=1)).isoformat(),
                expires_at=(NOW + timedelta(minutes=15)).isoformat(),
            ),
        }
        for expected, document in cases.items():
            with self.subTest(expected=expected), TemporaryDirectory() as directory:
                path = _write_permit(directory, document)
                with self.assertRaisesRegex(DeploymentAuthorizationError, expected):
                    _authorize(path)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_two_distinct_people_and_operator_checklist_are_required(self) -> None:
        cases = (
            (_permit(safety_observer="Operator-A"), "distinct operator"),
            (_permit(operator_checklist_confirmed=False), "invalid"),
        )
        for document, expected in cases:
            with self.subTest(expected=expected), TemporaryDirectory() as directory:
                path = _write_permit(directory, document)
                with self.assertRaisesRegex(DeploymentAuthorizationError, expected):
                    _authorize(path)

    @patch("brain.safety.deployment._PHYSICAL_ACTUATION_RELEASED", True)
    def test_writable_permit_is_refused(self) -> None:
        with TemporaryDirectory() as directory:
            path = _write_permit(directory, _permit())
            path.chmod(0o620)
            with self.assertRaisesRegex(DeploymentAuthorizationError, "group- or world-writable"):
                _authorize(path)

    def test_request_hash_is_canonical(self) -> None:
        first = actuation_request_sha256({"operation": OPERATION, "altitude": 2})
        second = actuation_request_sha256({"altitude": 2, "operation": OPERATION})
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
