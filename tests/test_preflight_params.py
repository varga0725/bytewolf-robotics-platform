"""Unit tests for the read-only PX4 preflight parameter checker."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.profile import PreflightCheckProfile
from brain.preflight_params.report import format_text, write_json
from brain.preflight_params.rules import RULES
from brain.preflight_params.sources import parse_qgc_params_file


_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "preflight_params" / "sample_qgc.params"


def _baseline_params() -> dict[str, float]:
    return {
        "NAV_DLL_ACT": 2.0,
        "COM_DL_LOSS_T": 10.0,
        "COM_RC_IN_MODE": 1.0,
        "COM_LOW_BAT_ACT": 3.0,
        "GF_ACTION": 2.0,
        "GF_MAX_HOR_DIST": 200.0,
        "GF_MAX_VER_DIST": 30.0,
        "COM_ARM_WO_GPS": 0.0,
        "NAV_RCL_ACT": 2.0,
        "COM_RC_LOSS_T": 0.5,
    }


class PreflightEvaluateTests(unittest.TestCase):
    def test_all_core_rules_pass_with_baseline_snapshot(self) -> None:
        report = evaluate_params(_baseline_params())
        core = [row for row in report.results if row.group == "core"]
        self.assertTrue(all(row.status == "PASS" for row in core), core)
        self.assertFalse(report.has_fail)

    def test_missing_parameter_is_fail_not_readable(self) -> None:
        params = _baseline_params()
        del params["NAV_DLL_ACT"]
        report = evaluate_params(params)
        row = next(r for r in report.results if r.param == "NAV_DLL_ACT")
        self.assertEqual(row.status, "FAIL")
        self.assertEqual(row.reason, "not readable")

    def test_nav_dll_act_zero_is_fail(self) -> None:
        params = _baseline_params()
        params["NAV_DLL_ACT"] = 0.0
        row = next(r for r in evaluate_params(params).results if r.param == "NAV_DLL_ACT")
        self.assertEqual(row.status, "FAIL")

    def test_nav_dll_act_land_requires_profile(self) -> None:
        params = _baseline_params()
        params["NAV_DLL_ACT"] = 3.0
        without = next(r for r in evaluate_params(params).results if r.param == "NAV_DLL_ACT")
        self.assertEqual(without.status, "FAIL")
        with_profile = next(
            r
            for r in evaluate_params(params, profile=PreflightCheckProfile(accept_nav_dll_land=True)).results
            if r.param == "NAV_DLL_ACT"
        )
        self.assertEqual(with_profile.status, "PASS")

    def test_com_dl_loss_t_bounds(self) -> None:
        for bad in (0.0, -1.0, 10.5, 300.0):
            params = _baseline_params()
            params["COM_DL_LOSS_T"] = bad
            row = next(r for r in evaluate_params(params).results if r.param == "COM_DL_LOSS_T")
            self.assertEqual(row.status, "FAIL", bad)

    def test_com_rc_in_mode_must_be_one(self) -> None:
        params = _baseline_params()
        params["COM_RC_IN_MODE"] = 3.0
        row = next(r for r in evaluate_params(params).results if r.param == "COM_RC_IN_MODE")
        self.assertEqual(row.status, "FAIL")

    def test_com_low_bat_act_must_be_three(self) -> None:
        params = _baseline_params()
        params["COM_LOW_BAT_ACT"] = 0.0
        row = next(r for r in evaluate_params(params).results if r.param == "COM_LOW_BAT_ACT")
        self.assertEqual(row.status, "FAIL")

    def test_geofence_rules(self) -> None:
        params = _baseline_params()
        params["GF_ACTION"] = 0.0
        self.assertEqual(
            next(r for r in evaluate_params(params).results if r.param == "GF_ACTION").status, "FAIL"
        )
        params = _baseline_params()
        params["GF_MAX_HOR_DIST"] = 0.0
        self.assertEqual(
            next(r for r in evaluate_params(params).results if r.param == "GF_MAX_HOR_DIST").status, "FAIL"
        )
        params = _baseline_params()
        params["GF_MAX_VER_DIST"] = 0.0
        self.assertEqual(
            next(r for r in evaluate_params(params).results if r.param == "GF_MAX_VER_DIST").status, "FAIL"
        )

    def test_extra_com_arm_wo_gps_warns_when_not_deny(self) -> None:
        params = _baseline_params()
        params["COM_ARM_WO_GPS"] = 1.0
        row = next(r for r in evaluate_params(params).results if r.param == "COM_ARM_WO_GPS")
        self.assertEqual(row.status, "WARN")

    def test_extra_informational_params_are_warn_when_present(self) -> None:
        report = evaluate_params(_baseline_params())
        for name in ("NAV_RCL_ACT", "COM_RC_LOSS_T"):
            row = next(r for r in report.results if r.param == name)
            self.assertEqual(row.status, "WARN")
            self.assertIn("informational", row.reason)

    def test_every_rule_has_a_unit_test_row(self) -> None:
        self.assertEqual(len(RULES), 10)

    def test_offline_fixture_passes_core_rules(self) -> None:
        params = parse_qgc_params_file(_FIXTURE)
        report = evaluate_params(params)
        core = [row for row in report.results if row.group == "core"]
        self.assertTrue(all(row.status == "PASS" for row in core))

    def test_json_report_round_trip(self) -> None:
        report = evaluate_params(_baseline_params())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "report.json"
            write_json(report, path)
            payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["summary"], report.summary)
        self.assertEqual(len(payload["results"]), len(report.results))

    def test_text_report_contains_status_markers(self) -> None:
        text = format_text(evaluate_params(_baseline_params()))
        self.assertIn("[PASS]", text)
        self.assertIn("COM_RC_IN_MODE", text)


class PreflightCliExitCodeTests(unittest.TestCase):
    def test_exit_code_mapping(self) -> None:
        from brain.preflight_params.cli import exit_code_for_report

        ok = evaluate_params(_baseline_params())
        bad = evaluate_params({})
        self.assertEqual(exit_code_for_report(ok, read_error=False), 0)
        self.assertEqual(exit_code_for_report(bad, read_error=False), 1)
        self.assertEqual(exit_code_for_report(bad, read_error=True), 2)


if __name__ == "__main__":
    unittest.main()