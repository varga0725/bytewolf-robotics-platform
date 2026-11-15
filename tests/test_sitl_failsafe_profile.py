"""SITL-only failsafe parameter profile (BYT-64)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from simulation.gazebo.fault_injection import PX4_BUILD_DIRECTORY, AppliedParameter, FaultInjectionError

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.param_value import MAV_PARAM_TYPE_INT32, MAV_PARAM_TYPE_REAL32, ParamValue
from brain.preflight_params.rules import RULES
from brain.preflight_params.sources import parse_qgc_params_file
from simulation.px4 import apply_sitl_failsafe_profile as apply_cli
from simulation.px4.sitl_failsafe_profile import (
    SitlFailsafeProfileError,
    apply_sitl_failsafe_profile,
    profile_applied_in_run_directory,
    refuse_apply_without_sitl_indicators,
    sitl_failsafe_parameter_tuples,
    sitl_failsafe_params_file,
    sitl_failsafe_status_path,
    verify_sitl_failsafe_profile,
    write_sitl_failsafe_status,
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

    def test_refuses_gz_autostart_without_sim_gz_en(self) -> None:
        with self.assertRaises(SitlFailsafeProfileError):
            refuse_apply_without_sitl_indicators(sys_autostart=4001, sim_gz_en=0, sih_vehicle_type=None)


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


class SitlFailsafeStatusAndVerifyTests(unittest.TestCase):
    def test_status_file_records_applied_and_failed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            write_sitl_failsafe_status(run_dir, "applied", reason="count=11")
            self.assertTrue(profile_applied_in_run_directory(run_dir))
            text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
            self.assertIn("applied", text)
            write_sitl_failsafe_status(run_dir, "failed", reason="gate")
            self.assertFalse(profile_applied_in_run_directory(run_dir))

    def test_verify_detects_mismatched_values(self) -> None:
        expected = dict(sitl_failsafe_parameter_tuples())

        def read(name: str, _directory: Path) -> float:
            return expected[name]

        verify_sitl_failsafe_profile(px4_build_directory=Path("build"), read_px4_parameter=read)

        def bad_read(name: str, _directory: Path) -> float:
            value = expected[name]
            return value + (1.0 if name == "GF_MAX_HOR_DIST" else 0.0)

        with self.assertRaises(SitlFailsafeProfileError):
            verify_sitl_failsafe_profile(px4_build_directory=Path("build"), read_px4_parameter=bad_read)

    def test_apply_helper_stops_retry_on_exit_code_two(self) -> None:
        root = Path(__file__).resolve().parents[1]
        helper = (root / "simulation/gazebo/launch/apply_sitl_failsafe_profile.zsh").read_text(encoding="utf-8")
        self.assertIn("exit_code=$?", helper)
        self.assertIn("(( exit_code == 2 ))", helper)
        self.assertIn("return 2", helper)
        self.assertIn('( cd "$project_root"', helper)

    def test_default_px4_build_directory_is_repo_relative(self) -> None:
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(PX4_BUILD_DIRECTORY, root / "PX4-Autopilot/build/px4_sitl_default")

    def test_cli_help_runs_from_foreign_cwd_without_pythonpath(self) -> None:
        root = Path(__file__).resolve().parents[1]
        env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
        with tempfile.TemporaryDirectory() as tmp:
            proc = subprocess.run(
                [
                    "bash",
                    "-c",
                    f'cd "{root}" && exec "{sys.executable}" -m simulation.px4.apply_sitl_failsafe_profile --help',
                ],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("SITL-only failsafe", proc.stdout)


class SitlFailsafeCliMainTests(unittest.TestCase):
    def _run_dir(self) -> tuple[Path, list[str]]:
        tmp = tempfile.mkdtemp()
        run_dir = Path(tmp)
        base = ["--px4-build-directory", str(run_dir), "--px4-run-directory", str(run_dir)]
        return run_dir, base

    @patch("simulation.px4.apply_sitl_failsafe_profile.apply_sitl_failsafe_profile")
    def test_gate_refusal_returns_two_and_writes_failed_with_reason(self, apply_mock: Mock) -> None:
        apply_mock.side_effect = SitlFailsafeProfileError("Refusing SITL gate")
        run_dir, args = self._run_dir()
        code = apply_cli.main(args)
        self.assertEqual(code, 2)
        text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("failed\n"))
        self.assertIn("Refusing SITL gate", text)

    @patch("simulation.px4.apply_sitl_failsafe_profile.apply_sitl_failsafe_profile")
    def test_unready_client_returns_one_and_writes_failed(self, apply_mock: Mock) -> None:
        apply_mock.side_effect = FaultInjectionError("PX4 parameter client timed out")
        run_dir, args = self._run_dir()
        code = apply_cli.main(args)
        self.assertEqual(code, 1)
        text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("failed\n"))
        self.assertIn("timed out", text)

    @patch("simulation.px4.apply_sitl_failsafe_profile.verify_sitl_failsafe_profile")
    @patch(
        "simulation.px4.apply_sitl_failsafe_profile.profile_applied_in_run_directory",
        return_value=True,
    )
    def test_verify_mismatch_returns_two_and_writes_failed(self, _applied: Mock, verify_mock: Mock) -> None:
        verify_mock.side_effect = SitlFailsafeProfileError("GF_MAX_HOR_DIST: expected 100, got 0")
        run_dir, args = self._run_dir()
        code = apply_cli.main([*args, "--verify"])
        self.assertEqual(code, 2)
        text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("failed\n"))
        self.assertIn("GF_MAX_HOR_DIST", text)

    def test_verify_without_applied_status_returns_three(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            code = apply_cli.main(
                [
                    "--px4-build-directory",
                    str(run_dir),
                    "--px4-run-directory",
                    str(run_dir),
                    "--verify",
                ]
            )
            self.assertEqual(code, 3)
            text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("failed\n"))
            self.assertIn("does not record applied", text)

    @patch("simulation.px4.apply_sitl_failsafe_profile.apply_sitl_failsafe_profile")
    def test_success_writes_applied_status(self, apply_mock: Mock) -> None:
        applied = tuple(
            AppliedParameter("GF_MAX_HOR_DIST", 100.0, 100.0) for _ in range(3)
        )
        apply_mock.return_value = applied
        run_dir, args = self._run_dir()
        code = apply_cli.main(args)
        self.assertEqual(code, 0)
        text = sitl_failsafe_status_path(run_dir).read_text(encoding="utf-8")
        self.assertTrue(text.startswith("applied\n"))
        self.assertIn("count=3", text)


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