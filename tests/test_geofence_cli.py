"""The geofence probe must record rejection without connecting or arming."""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from brain.cli import check_geofence_violation
from brain.safety.gate import SafetyViolation


class GeofenceProbeCliTests(unittest.TestCase):
    def test_rejected_violation_writes_a_no_flight_artifact_and_exits_successfully(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            # No explicit target: the probe has to find a violating one from the
            # active contract. A fixed distance here would silently start
            # proving the opposite the moment the fence widened.
            arguments = check_geofence_violation.parse_arguments(
                ("--east", "0", "--artifact-dir", directory)
            )

            asyncio.run(check_geofence_violation.run(arguments))

            artifact_paths = list(Path(directory).glob("*.json"))
            self.assertEqual(len(artifact_paths), 1)
            artifact = json.loads(artifact_paths[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["events"], [])
            self.assertEqual(artifact["safety_decision"], "rejected")
            self.assertEqual(artifact["outcome"], "completed")
            self.assertIn("geofence", artifact["failure_reason"])

    def test_safe_target_fails_the_probe_instead_of_claiming_a_violation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            arguments = check_geofence_violation.parse_arguments(
                ("--north", "5", "--east", "5", "--artifact-dir", directory)
            )

            with self.assertRaisesRegex(SafetyViolation, "inside"):
                asyncio.run(check_geofence_violation.run(arguments))

            artifact = json.loads(next(Path(directory).glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(artifact["events"], [])
            self.assertEqual(artifact["safety_decision"], "approved")
            self.assertEqual(artifact["outcome"], "failed")

    def test_the_derived_target_is_outside_whatever_fence_is_configured(self) -> None:
        """The probe's default must follow the contract, not a remembered number.

        It flew at a fixed 45 m, which violated the fence only because the fence
        was a 30 m box. Widening the contract to 2 km turned that same probe
        into one that proves a waypoint is *approved* — a passing safety
        scenario asserting the opposite of its own name.
        """
        from brain.cli.check_geofence_violation import _just_outside_the_fence
        from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile

        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        north_m = _just_outside_the_fence(profile)

        assert profile.allowed_geofence is not None
        self.assertFalse(profile.allowed_geofence.contains(north_m, 0.0))


if __name__ == "__main__":
    unittest.main()
