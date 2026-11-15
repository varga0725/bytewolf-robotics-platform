"""Call-site delegation to the shared connection policy."""

from __future__ import annotations

import unittest

from brain.mission.demo_errors import DemoPointRtlError
from brain.mission.simulation_target import require_simulation_target
from brain.safety.deployment import DeploymentAuthorizationError, authorize_readonly_connection
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


_PROFILE = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)


class SimulationTargetDelegationTests(unittest.TestCase):
    def test_delegates_localhost_rejection(self) -> None:
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="simulation",
                endpoint="udpin://localhost:14540",
                profile=_PROFILE,
                operation="demo-point-rtl",
                request_sha256="abc",
            )


class DeploymentSimulationEndpointDelegationTests(unittest.TestCase):
    def test_delegates_bind_all_rejection(self) -> None:
        with self.assertRaises(DeploymentAuthorizationError):
            authorize_readonly_connection("simulation", "udpin://0.0.0.0:14540")

    def test_accepts_canonical_loopback_udpin(self) -> None:
        authorize_readonly_connection("simulation", "udpin://127.0.0.1:14540")


if __name__ == "__main__":
    unittest.main()