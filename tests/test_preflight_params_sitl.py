"""Live SITL smoke for preflight parameter checker (optional)."""

from __future__ import annotations

import os
import unittest

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.sources import load_params_from_connection


@unittest.skipUnless(
    os.environ.get("BYTEWOLF_SITL_CONNECTION"),
    "Set BYTEWOLF_SITL_CONNECTION to a running PX4 SITL MAVLink URL",
)
class PreflightParamsSitlIntegrationTests(unittest.TestCase):
    def test_downloads_params_and_produces_per_rule_results(self) -> None:
        connection = os.environ["BYTEWOLF_SITL_CONNECTION"]
        timeout = float(os.environ.get("BYTEWOLF_SITL_CONNECTION_TIMEOUT", "60"))
        params = load_params_from_connection(connection, timeout_s=timeout)
        self.assertGreater(len(params), 50, "expected a full PX4 parameter set")
        report = evaluate_params(params)
        self.assertEqual(len(report.results), 10)
        for row in report.results:
            with self.subTest(param=row.param):
                self.assertIn(row.status, ("PASS", "WARN", "FAIL"))
                self.assertIsNotNone(row.expected)
        # Stock SITL often violates NAV_DLL_ACT=0; structure matters more than PASS.
        nav_dll = next(r for r in report.results if r.param == "NAV_DLL_ACT")
        actual = params.get("NAV_DLL_ACT")
        if actual is not None and int(round(actual)) == 0:
            self.assertEqual(nav_dll.status, "FAIL")


if __name__ == "__main__":
    unittest.main()