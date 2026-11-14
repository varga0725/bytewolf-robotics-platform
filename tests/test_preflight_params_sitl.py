"""Live SITL integration for preflight parameter checker (optional)."""

from __future__ import annotations

import os
import unittest

from brain.preflight_params.cli import parse_arguments, run_checked
from brain.preflight_params.rules import RULES


@unittest.skipUnless(
    os.environ.get("BYTEWOLF_SITL_CONNECTION"),
    "Set BYTEWOLF_SITL_CONNECTION to a running PX4 SITL MAVLink URL",
)
class PreflightParamsSitlIntegrationTests(unittest.TestCase):
    def test_cli_authorizes_decodes_and_reports_rule_params(self) -> None:
        connection = os.environ["BYTEWOLF_SITL_CONNECTION"]
        timeout = float(os.environ.get("BYTEWOLF_SITL_CONNECTION_TIMEOUT", "60"))
        args = parse_arguments(
            [
                "--connection",
                connection,
                "--deployment-mode",
                "simulation",
                "--timeout",
                str(timeout),
            ]
        )
        exit_code, report, params = run_checked(args)
        self.assertIsNotNone(params)
        self.assertIsNotNone(report)
        self.assertGreater(len(params), 50, "expected a full PX4 parameter set")
        self.assertEqual(len(report.results), len(RULES))

        for rule in RULES:
            entry = params.get(rule.param)
            with self.subTest(param=rule.param):
                self.assertIsNotNone(entry, "rule parameter must be present on SITL")
                assert entry is not None
                if entry.is_integer:
                    self.assertIsInstance(entry.display_value, int)
                scalar = float(entry.display_value)
                if scalar != 0.0:
                    self.assertGreaterEqual(
                        abs(scalar),
                        1e-30,
                        f"{rule.param} must not be a denormal mis-decode of an integer",
                    )
                row = next(r for r in report.results if r.param == rule.param)
                self.assertIn(row.status, ("PASS", "WARN", "FAIL"))

        gz_int = {
            "NAV_DLL_ACT": 2,
            "COM_DL_LOSS_T": 10,
            "COM_RC_IN_MODE": 1,
            "COM_LOW_BAT_ACT": 3,
            "GF_ACTION": 2,
            "COM_ARM_WO_GPS": 1,
            "NAV_RCL_ACT": 2,
        }
        gz_float = {"GF_MAX_HOR_DIST": 0.0, "GF_MAX_VER_DIST": 0.0, "COM_RC_LOSS_T": 0.5}
        matches_stock = all(params[name].display_value == expected for name, expected in gz_int.items()) and all(
            abs(float(params[name].display_value) - expected) < 1e-6 for name, expected in gz_float.items()
        )
        if matches_stock:
            self.assertEqual(report.summary, "FAIL (2 failed, 3 warn, 5 pass)")
            self.assertEqual(exit_code, 1)


if __name__ == "__main__":
    unittest.main()