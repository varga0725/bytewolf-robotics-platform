"""SITL-only failsafe parameter profile (BYT-64)."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import Mock

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.param_value import MAV_PARAM_TYPE_INT32, MAV_PARAM_TYPE_REAL32, ParamValue
from brain.preflight_params.rules import RULES
from brain.preflight_params.sources import parse_qgc_params_file
from simulation.px4.sitl_failsafe_profile import (
    SitlFailsafeProfileError,
    apply_sitl_failsafe_profile,
    refuse_apply_without_sitl_indicators,
    sitl_failsafe_parameter_tuples,
    sitl_failsafe_params_file,
)


def _param(value: int | float, *, integer: bool = True) -> ParamValue:
    if integer:
        return ParamValue(int(value), MAV_PARAM_TYPE_INT32)
    return ParamValue(float(value), MAV_PARAM_TYPE_REAL32)


class SitlFailsafeGuardTests(unittest.TestCase):
    def test_refuses_pixhawk_like_autostart(self) -> None:
        with self.assertRaises(SitlFailsafeProfileError):
            refuse_apply_without_sitl_indicators(sys_autostart=0, sim_gz_en=None, sih_vehicle_type=None)

    def test_accepts_gz_sitl_indicators(self) -> None:
        matched = refuse_apply_without_sitl_indicators(sys_autostart=4001, sim_gz_en=1, sih_vehicle_type=None)
        self.assertIn("sys_autostart_posix_sitl", matched)
        self.assertIn("sim_gz_en", matched)


class SitlFailsafeApplyTests(unittest.TestCase):
    def test_apply_refuses_without_sitl_before_any_set(self) -> None:
        reads = {"SYS_AUTOSTART": 0}

        def read(name: str, _directory: Path) -> float:
            return float(reads[name])

        apply_mock = Mock()
        with self.assertRaises(SitlFailsafeProfileError):
            apply_sitl_failsafe_profile(
                px4_build_directory=Path("build"),
                read_px4_parameter=read,
                apply_parameters=apply_mock,
            )
        apply_mock.assert_not_called()

    def test_apply_runs_after_sitl_gate(self) -> None:
        reads = {"SYS_AUTOSTART": 4014, "SIM_GZ_EN": 1}
        calls: list[str] = []

        def read(name: str, _directory: Path) -> float:
            calls.append(name)
            return float(reads[name])

        apply_mock = Mock(return_value=())
        apply_sitl_failsafe_profile(
            px4_build_directory=Path("build"),
            read_px4_parameter=read,
            apply_parameters=apply_mock,
        )
        apply_mock.assert_called_once()
        parameters = apply_mock.call_args.args[0]
        self.assertEqual(parameters, sitl_failsafe_parameter_tuples())


class SitlFailsafePreflightTests(unittest.TestCase):
    def test_preflight_core_rules_pass_with_profile_snapshot(self) -> None:
        parsed = parse_qgc_params_file(sitl_failsafe_params_file())
        # Preflight rules also expect RC-mode and visibility params not in the failsafe file.
        merged = {
            **{name: _param(value.display_value, integer=value.is_integer) for name, value in parsed.items()},
            "COM_RC_IN_MODE": _param(1),
            "COM_ARM_WO_GPS": _param(0),
            "NAV_RCL_ACT": _param(2),
            "COM_RC_LOSS_T": _param(0.5, integer=False),
        }
        report = evaluate_params(merged)
        core = [row for row in report.results if row.group == "core"]
        self.assertEqual(len(core), len([rule for rule in RULES if rule.group == "core"]))
        failures = [row for row in core if row.status == "FAIL"]
        self.assertEqual(failures, [], failures)
        for name in ("GF_MAX_HOR_DIST", "GF_MAX_VER_DIST"):
            row = next(r for r in report.results if r.param == name)
            self.assertEqual(row.status, "PASS")
            self.assertIn("m", row.reason)


if __name__ == "__main__":
    unittest.main()