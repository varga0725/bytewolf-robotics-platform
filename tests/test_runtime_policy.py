from dataclasses import FrozenInstanceError
from pathlib import Path
import tempfile
import unittest

from brain.mission.runtime_policy import RuntimePolicyError, load_runtime_policy


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "platforms/x500v2/config/runtime_policy.v0_1.yaml"


class RuntimePolicyTests(unittest.TestCase):
    def test_loads_an_immutable_versioned_runtime_policy(self) -> None:
        policy = load_runtime_policy(POLICY_PATH)

        self.assertEqual(policy.version, "v0.1")
        self.assertEqual(policy.waypoint_timeout_s, 30.0)
        self.assertEqual(policy.landing_confirmation_timeout_s, 60.0)
        self.assertEqual(policy.fallback_land_attempts, 1)
        with self.assertRaises(FrozenInstanceError):
            policy.waypoint_timeout_s = 10.0  # type: ignore[misc]

    def test_rejects_a_policy_that_allows_actuation_retries(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            policy_path = Path(directory) / "policy.yaml"
            policy_path.write_text(
                "version: v0.1\n"
                "timeouts:\n"
                "  waypoint_s: 30\n"
                "  landing_confirmation_s: 60\n"
                "failure_handling:\n"
                "  fallback_land_attempts: 2\n"
            )

            with self.assertRaisesRegex(RuntimePolicyError, "fallback_land_attempts"):
                load_runtime_policy(policy_path)


if __name__ == "__main__":
    unittest.main()
