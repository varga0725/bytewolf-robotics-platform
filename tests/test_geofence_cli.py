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
            arguments = check_geofence_violation.parse_arguments(
                ("--north", "45", "--east", "0", "--artifact-dir", directory)
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


if __name__ == "__main__":
    unittest.main()
