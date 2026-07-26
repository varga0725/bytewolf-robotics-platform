"""Coverage for applying PX4's own fault-injection parameters.

A scenario that claims a fault must prove PX4 actually held it, so the write is
always read back and an unconfirmed write fails closed.
"""

from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock

from simulation.gazebo.fault_injection import FaultInjectionError, apply_px4_parameters


def _param_output(name: str, value: str, symbols: str = "x * ") -> str:
    """Mirror PX4's real output.

    A parameter that was just set reads back as used *and* unsaved, so it
    carries two symbols -- which is the only state a read-back ever sees.
    """
    return (
        "Symbols: x = used, + = saved, * = unsaved\n"
        f"{symbols}{name} [836,1417] : {value}\n\n 1000/1920 parameters used.\n"
    )


def _runner(values: dict[str, str], returncode: int = 0) -> Mock:
    """Fake PX4's parameter client: 'set' succeeds, 'show' reports `values`."""

    def run(command, **_kwargs):
        if command[1] == "show":
            return Mock(returncode=returncode, stdout=_param_output(command[2], values[command[2]]), stderr="")
        return Mock(returncode=returncode, stdout="", stderr="")

    return Mock(side_effect=run)


class ApplyPx4ParametersTests(unittest.TestCase):
    def test_sets_each_parameter_and_reports_what_px4_confirmed(self) -> None:
        run = _runner({"SIM_BAT_DRAIN": "20.0000", "SIM_BAT_MIN_PCT": "20.0000"})

        applied = apply_px4_parameters(
            (("SIM_BAT_DRAIN", 20.0), ("SIM_BAT_MIN_PCT", 20.0)),
            px4_build_directory=Path("build"),
            run_command=run,
        )

        self.assertEqual([parameter.name for parameter in applied], ["SIM_BAT_DRAIN", "SIM_BAT_MIN_PCT"])
        self.assertEqual([parameter.confirmed for parameter in applied], [20.0, 20.0])
        self.assertIn(("bin/px4-param", "set", "SIM_BAT_DRAIN", "20"), [call.args[0] for call in run.call_args_list])

    def test_tolerates_px4s_float32_read_back(self) -> None:
        applied = apply_px4_parameters(
            (("SIM_BAT_MIN_PCT", 20.1),),
            px4_build_directory=Path("build"),
            run_command=_runner({"SIM_BAT_MIN_PCT": "20.1000"}),
        )

        self.assertEqual(applied[0].requested, 20.1)

    def test_refuses_a_parameter_px4_did_not_take(self) -> None:
        """An unconfirmed write would let a scenario claim a fault it never had."""
        run = _runner({"SIM_BAT_DRAIN": "60.0000"})

        with self.assertRaisesRegex(FaultInjectionError, "reads back 60"):
            apply_px4_parameters((("SIM_BAT_DRAIN", 20.0),), px4_build_directory=Path("build"), run_command=run)

    def test_reads_every_status_symbol_combination_px4_emits(self) -> None:
        """PX4 marks a parameter used, saved, unsaved, or several at once."""
        for symbols in ("x * ", "x   ", "x + ", "* ", "    "):
            with self.subTest(symbols=symbols):
                run = Mock(
                    side_effect=lambda command, _s=symbols, **_k: Mock(
                        returncode=0,
                        stdout=_param_output("SIM_BAT_DRAIN", "20.0000", _s) if command[1] == "show" else "",
                        stderr="",
                    )
                )

                applied = apply_px4_parameters(
                    (("SIM_BAT_DRAIN", 20.0),), px4_build_directory=Path("build"), run_command=run
                )

                self.assertEqual(applied[0].confirmed, 20.0)

    def test_refuses_a_parameter_px4_does_not_report(self) -> None:
        run = Mock(return_value=Mock(returncode=0, stdout="no such parameter\n", stderr=""))

        with self.assertRaisesRegex(FaultInjectionError, "did not report a value"):
            apply_px4_parameters((("NOPE", 1.0),), px4_build_directory=Path("build"), run_command=run)

    def test_refuses_a_non_finite_value(self) -> None:
        for value in (float("nan"), float("inf")):
            with self.subTest(value=value), self.assertRaisesRegex(FaultInjectionError, "finite value"):
                apply_px4_parameters(
                    (("SIM_BAT_DRAIN", value),), px4_build_directory=Path("build"), run_command=Mock()
                )

    def test_reports_a_failing_or_absent_parameter_client(self) -> None:
        failing = Mock(return_value=Mock(returncode=1, stdout="", stderr="daemon not running"))
        with self.assertRaisesRegex(FaultInjectionError, "daemon not running"):
            apply_px4_parameters((("SIM_BAT_DRAIN", 20.0),), px4_build_directory=Path("build"), run_command=failing)

        missing = Mock(side_effect=OSError("No such file"))
        with self.assertRaisesRegex(FaultInjectionError, "Cannot run PX4's parameter client"):
            apply_px4_parameters((("SIM_BAT_DRAIN", 20.0),), px4_build_directory=Path("build"), run_command=missing)

        slow = Mock(side_effect=subprocess.TimeoutExpired("px4-param", 10.0))
        with self.assertRaisesRegex(FaultInjectionError, "timed out"):
            apply_px4_parameters((("SIM_BAT_DRAIN", 20.0),), px4_build_directory=Path("build"), run_command=slow)

    def test_retries_a_transient_parameter_client_timeout_before_confirming_the_fault(self) -> None:
        """A delayed PX4 daemon must not turn a confirmed fault into a blocked run."""
        successful = _runner({"SIM_BAT_DRAIN": "20.0000"})
        attempts = 0

        def run(command, **kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise subprocess.TimeoutExpired("px4-param", 10.0)
            return successful(command, **kwargs)

        sleep = Mock()

        applied = apply_px4_parameters(
            (("SIM_BAT_DRAIN", 20.0),),
            px4_build_directory=Path("build"),
            run_command=Mock(side_effect=run),
            sleep=sleep,
        )

        self.assertEqual(applied[0].confirmed, 20.0)
        self.assertEqual(attempts, 3)
        sleep.assert_called_once_with(1.0)

    def test_fails_closed_after_bounded_parameter_client_timeouts(self) -> None:
        run = Mock(side_effect=subprocess.TimeoutExpired("px4-param", 10.0))
        sleep = Mock()

        with self.assertRaisesRegex(FaultInjectionError, r"timed out .* after 3 attempts"):
            apply_px4_parameters(
                (("SIM_BAT_DRAIN", 20.0),),
                px4_build_directory=Path("build"),
                run_command=run,
                sleep=sleep,
            )

        self.assertEqual(run.call_count, 3)
        self.assertEqual(sleep.call_count, 2)


if __name__ == "__main__":
    unittest.main()
