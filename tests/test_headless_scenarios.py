"""Unit tests for the manual headless P0 scenario runner."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from simulation.scenarios.scenarios import (
    P0_SCENARIOS,
    P0_V2_SCENARIOS,
    Scenario,
    ScenarioRunner,
    parse_arguments,
)


class HeadlessScenarioTests(unittest.TestCase):
    def test_p0_matrix_covers_the_bounded_flight_commands(self) -> None:
        self.assertEqual(
            tuple(scenario.identifier for scenario in P0_SCENARIOS),
            (
                "takeoff-hover-land", "waypoint-land", "return-to-home", "reject-unsafe-altitude",
                "waypoint-timeout-fallback", "link-unavailable",
            ),
        )
        self.assertTrue(all(scenario.module.startswith("brain.cli.") for scenario in P0_SCENARIOS))
        self.assertTrue(all(scenario.version == "p0.v1" for scenario in P0_SCENARIOS))
        self.assertTrue(all(scenario.readiness_requirements for scenario in P0_SCENARIOS))
        self.assertTrue(all(scenario.safety_rejection is not None for scenario in P0_SCENARIOS))
        self.assertTrue(all(scenario.fallback_expectation for scenario in P0_SCENARIOS))
        self.assertEqual(P0_SCENARIOS[-1].expected_returncode, 1)

    def test_p0_v2_adds_explicit_boot_prearm_evidence_and_exact_nominal_profile(self) -> None:
        """P0.v1 stays stable while P0.v2 makes the requested nominal evidence explicit."""
        self.assertEqual(
            tuple(scenario.identifier for scenario in P0_V2_SCENARIOS[:2]),
            ("boot-prearm-check", "takeoff-2m-hover-10s-land"),
        )

    def test_runner_can_select_the_expanded_p0_v2_matrix_without_replacing_v1(self) -> None:
        self.assertEqual(parse_arguments(()).matrix_version, "p0.v1")
        self.assertEqual(parse_arguments(("--matrix-version", "p0.v2")).matrix_version, "p0.v2")
        self.assertEqual(P0_V2_SCENARIOS[0].module, "brain.cli.check_boot_prearm")
        self.assertEqual(P0_V2_SCENARIOS[0].arguments, ("--preflight-wait-seconds", "60"))
        self.assertEqual(
            P0_V2_SCENARIOS[1].arguments,
            ("--altitude", "2", "--hover-seconds", "10", "--preflight-wait-seconds", "60"),
        )
        self.assertTrue(all(scenario.version == "p0.v2" for scenario in P0_V2_SCENARIOS))
        self.assertEqual(
            tuple(scenario.identifier for scenario in P0_SCENARIOS),
            (
                "takeoff-hover-land", "waypoint-land", "return-to-home", "reject-unsafe-altitude",
                "waypoint-timeout-fallback", "link-unavailable",
            ),
        )

    def test_p0_matrix_allows_bounded_telemetry_readiness_after_sitl_startup(self) -> None:
        """The P0 process budget retains time for the actual mission after readiness."""
        for scenario in P0_SCENARIOS:
            if scenario.identifier in {"reject-unsafe-altitude", "link-unavailable"}:
                continue
            self.assertIn("--preflight-wait-seconds", scenario.arguments)
            argument_index = scenario.arguments.index("--preflight-wait-seconds")
            self.assertEqual(scenario.arguments[argument_index + 1], "60")

    def test_runner_records_each_scenario_and_writes_a_json_report(self) -> None:
        completed = Mock(return_value=Mock(returncode=0, stdout="mission complete\n", stderr=""))
        timestamp = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(
                command_runner=completed,
                now=lambda: timestamp,
            ).run(P0_SCENARIOS[:1], output_directory=Path(temporary_directory))

            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(completed.call_count, 1)
        invocation = completed.call_args.args[0]
        self.assertEqual(invocation[1:3], ("-m", "brain.cli.fly_takeoff_hover_land"))
        self.assertIn("--mavsdk-server-port", invocation)
        self.assertIn('"status": "passed"', report)
        self.assertIn('"takeoff-hover-land"', report)
        self.assertIn("mission complete", report)

    def test_runner_uses_a_fresh_artifact_directory_for_each_invocation(self) -> None:
        completed = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        timestamp = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)

        with TemporaryDirectory() as temporary_directory:
            runner = ScenarioRunner(command_runner=completed, now=lambda: timestamp)
            first_report = json.loads(runner.run(P0_SCENARIOS[:1], Path(temporary_directory)).read_text())
            second_report = json.loads(runner.run(P0_SCENARIOS[:1], Path(temporary_directory)).read_text())

        first_directory = first_report["results"][0]["artifact_directory"]
        second_directory = second_report["results"][0]["artifact_directory"]
        self.assertNotEqual(first_directory, second_directory)

    def test_runner_assigns_distinct_mavsdk_ports_to_distinct_run_directories(self) -> None:
        completed = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
        timestamp = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)

        with TemporaryDirectory() as temporary_directory:
            runner = ScenarioRunner(command_runner=completed, now=lambda: timestamp)
            runner.run(P0_SCENARIOS[:1], Path(temporary_directory))
            first_port = completed.call_args.args[0][
                completed.call_args.args[0].index("--mavsdk-server-port") + 1
            ]
            runner.run(P0_SCENARIOS[:1], Path(temporary_directory))
            second_port = completed.call_args.args[0][
                completed.call_args.args[0].index("--mavsdk-server-port") + 1
            ]

        self.assertNotEqual(first_port, second_port)

    def test_runner_passes_a_expected_safety_rejection_and_assigns_artifact_directory(self) -> None:
        scenario = Scenario(
            "reject-unsafe-altitude",
            "brain.cli.fly_takeoff_hover_land",
            ("--altitude", "21"),
            safety_rejection="must-reject-over-max-altitude",
            fallback_expectation="no-flight-command",
            expected_returncode=1,
        )
        completed = Mock(return_value=Mock(returncode=1, stdout="", stderr="mission rejected"))

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(command_runner=completed).run(
                (scenario,), Path(temporary_directory)
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        result = report["results"][0]
        self.assertEqual(result["status"], "passed")
        self.assertEqual(result["expected_returncode"], 1)
        self.assertTrue(result["artifact_directory"].endswith("mission-artifacts/reject-unsafe-altitude"))
        self.assertIn("--artifact-dir", completed.call_args.args[0])

    def test_runner_includes_versioned_safety_metadata_in_json_report(self) -> None:
        scenario = Scenario(
            "safety-reject",
            "brain.cli.fly_waypoint_land",
            version="p0.v1",
            readiness_requirements=("mavsdk-connected", "telemetry-healthy"),
            safety_rejection="must-reject-out-of-bounds-waypoint",
            fallback_expectation="no-flight-command",
        )
        completed = Mock(return_value=Mock(returncode=0, stdout="rejected\n", stderr=""))

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(command_runner=completed).run(
                (scenario,), Path(temporary_directory)
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        result = report["results"][0]
        self.assertEqual(result["version"], "p0.v1")
        self.assertEqual(result["readiness_requirements"], ["mavsdk-connected", "telemetry-healthy"])
        self.assertEqual(result["safety_rejection"], "must-reject-out-of-bounds-waypoint")
        self.assertEqual(result["fallback_expectation"], "no-flight-command")

    def test_runner_starts_a_plain_launcher_process_and_cleans_it_up(self) -> None:
        sitl_process = Mock(pid=90210)
        process_starter = Mock(return_value=sitl_process)
        command_runner = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        readiness_check = Mock(return_value=True)
        terminate_process = Mock()
        sleep = Mock()
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("./simulation/gazebo/launch/run_px4_gazebo_headless.zsh", "base"),
            process_starter=process_starter,
            readiness_check=readiness_check,
            terminate_process=terminate_process,
            sleep=sleep,
        )

        with TemporaryDirectory() as temporary_directory:
            runner.run(P0_SCENARIOS[:1], Path(temporary_directory))

        self.assertEqual(process_starter.call_args.args[0], runner.sitl_command)
        self.assertNotIn("process_group", process_starter.call_args.kwargs)
        self.assertNotIn("start_new_session", process_starter.call_args.kwargs)
        self.assertIs(process_starter.call_args.kwargs["stdin"], subprocess.DEVNULL)
        self.assertIs(process_starter.call_args.kwargs["stdout"], subprocess.DEVNULL)
        self.assertIs(process_starter.call_args.kwargs["stderr"], subprocess.DEVNULL)
        readiness_check.assert_called_once_with(sitl_process)
        sleep.assert_called_once_with(45.0)
        terminate_process.assert_called_once_with(90210)

    def test_runner_reports_readiness_failure_and_still_cleans_up_sitl(self) -> None:
        sitl_process = Mock(pid=90210)
        command_runner = Mock()
        terminate_process = Mock()
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("sitl",),
            process_starter=Mock(return_value=sitl_process),
            readiness_check=Mock(return_value=False),
            terminate_process=terminate_process,
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = runner.run(P0_SCENARIOS[:1], Path(temporary_directory))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(command_runner.call_count, 0)
        self.assertEqual(report["overall_status"], "failed")
        self.assertEqual(report["results"][0]["status"], "blocked")
        terminate_process.assert_called_once_with(90210)

    def test_runner_retries_a_transient_sitl_start_failure(self) -> None:
        sitl_process = Mock(pid=90210)
        process_starter = Mock(side_effect=(OSError(1, "Operation not permitted"), sitl_process))
        command_runner = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        sleep = Mock()
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("sitl",),
            process_starter=process_starter,
            readiness_check=Mock(return_value=True),
            terminate_process=Mock(),
            sleep=sleep,
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = runner.run(P0_SCENARIOS[:1], Path(temporary_directory))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(process_starter.call_count, 2)
        self.assertEqual(report["overall_status"], "passed")
        sleep.assert_any_call(1.0)

    def test_isolated_cli_process_does_not_request_a_new_session_or_process_group(self) -> None:
        process = Mock(returncode=0)
        process.poll.return_value = 0
        process.communicate.return_value = ("ok", "")
        scenario = Scenario("takeoff-hover-land", "brain.cli.fly_takeoff_hover_land")

        with TemporaryDirectory() as temporary_directory:
            with patch("simulation.scenarios.scenarios.subprocess.Popen", return_value=process) as starter:
                ScenarioRunner()._run_isolated_scenario(
                    scenario,
                    ("python", "-m", scenario.module),
                    Path(temporary_directory) / "mission-artifacts" / scenario.identifier,
                )

        self.assertNotIn("process_group", starter.call_args.kwargs)
        self.assertNotIn("start_new_session", starter.call_args.kwargs)

    def test_runner_marks_a_nonzero_process_as_failed_without_stopping_matrix(self) -> None:
        command_runner = Mock(
            side_effect=(
                Mock(returncode=1, stdout="", stderr="connection failed"),
                Mock(returncode=0, stdout="ok", stderr=""),
            )
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(command_runner=command_runner).run(
                P0_SCENARIOS[:2], output_directory=Path(temporary_directory)
            )
            report = report_path.read_text(encoding="utf-8")

        self.assertEqual(command_runner.call_count, 2)
        self.assertIn('"status": "failed"', report)
        self.assertIn('"status": "passed"', report)

    def test_repeatability_report_requires_the_configured_success_rate_per_nominal_scenario(self) -> None:
        scenario = Scenario("takeoff-hover-land", "brain.cli.fly_takeoff_hover_land")
        completed = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        timestamp = datetime(2026, 7, 16, 10, 30, tzinfo=UTC)

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(
                command_runner=completed,
                now=lambda: timestamp,
            ).run_repeated(
                (scenario,),
                Path(temporary_directory),
                repetitions=10,
                minimum_success_rate=0.9,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["overall_status"], "passed")
        self.assertEqual(report["repetitions"], 10)
        self.assertEqual(report["success_rates"]["takeoff-hover-land"], 1.0)
        self.assertEqual(len(report["run_reports"]), 10)

    def test_repeatability_report_fails_when_a_nominal_scenario_misses_the_gate(self) -> None:
        scenario = Scenario("waypoint-land", "brain.cli.fly_waypoint_land")
        completed = Mock(
            side_effect=(
                *(Mock(returncode=0, stdout="ok", stderr="") for _ in range(8)),
                *(Mock(returncode=1, stdout="", stderr="failed") for _ in range(2)),
            )
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = ScenarioRunner(command_runner=completed).run_repeated(
                (scenario,),
                Path(temporary_directory),
                repetitions=10,
                minimum_success_rate=0.9,
            )
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(report["overall_status"], "failed")
        self.assertEqual(report["success_rates"]["waypoint-land"], 0.8)


if __name__ == "__main__":
    unittest.main()
