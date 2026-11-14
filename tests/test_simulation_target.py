"""Simulation-only target checks for demo missions."""

from __future__ import annotations

import unittest

from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.simulation_target import redact_loopback_endpoint, require_simulation_target
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


_PROFILE = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)


class SimulationTargetTests(unittest.TestCase):
    def test_loopback_udp_is_accepted(self) -> None:
        require_simulation_target(
            deployment_mode="simulation",
            endpoint="udpin://127.0.0.1:14540",
            profile=_PROFILE,
            operation="demo-point-rtl",
            request_sha256="abc",
        )

    def test_serial_endpoints_are_refused(self) -> None:
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="simulation",
                endpoint="serial:///dev/ttyACM0",
                profile=_PROFILE,
                operation="demo-point-rtl",
                request_sha256="abc",
            )

    def test_non_loopback_udp_is_refused(self) -> None:
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="simulation",
                endpoint="udpin://203.0.113.5:14540",
                profile=_PROFILE,
                operation="demo-point-rtl",
                request_sha256="abc",
            )

    def test_unknown_deployment_modes_are_refused(self) -> None:
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="bench_readonly",
                endpoint="udpin://127.0.0.1:14540",
                profile=_PROFILE,
                operation="demo-point-rtl",
                request_sha256="abc",
            )

    def test_physical_permits_are_refused(self) -> None:
        with self.assertRaises(DemoPointRtlError):
            require_simulation_target(
                deployment_mode="simulation",
                endpoint="udpin://127.0.0.1:14540",
                profile=_PROFILE,
                operation="demo-point-rtl",
                request_sha256="abc",
                permit_path=__import__("pathlib").Path("permit.json"),
            )

    def test_endpoints_are_redacted_for_evidence(self) -> None:
        self.assertEqual(
            redact_loopback_endpoint("udpin://127.0.0.1:14540"),
            {"transport": "udp-loopback", "port": 14540},
        )


if __name__ == "__main__":
    unittest.main()
