"""Unit tests for the manual headless P0 scenario runner."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from simulation.headless.scenarios import P0_SCENARIOS, Scenario, ScenarioRunner


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
        self.assertIn('"status": "passed"', report)
        self.assertIn('"takeoff-hover-land"', report)
        self.assertIn("mission complete", report)

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

    def test_runner_starts_sitl_in_its_own_process_group_and_cleans_it_up(self) -> None:
        sitl_process = Mock(pid=90210)
        process_starter = Mock(return_value=sitl_process)
        command_runner = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        readiness_check = Mock(return_value=True)
        terminate_group = Mock()
        sleep = Mock()
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("./simulation/launch/run_px4_gazebo_headless.zsh", "base"),
            process_starter=process_starter,
            readiness_check=readiness_check,
            terminate_process_group=terminate_group,
            sleep=sleep,
        )

        with TemporaryDirectory() as temporary_directory:
            runner.run(P0_SCENARIOS[:1], Path(temporary_directory))

        self.assertEqual(process_starter.call_args.args[0], runner.sitl_command)
        self.assertTrue(process_starter.call_args.kwargs["start_new_session"])
        readiness_check.assert_called_once_with(sitl_process)
        sleep.assert_called_once_with(45.0)
        terminate_group.assert_called_once_with(90210)

    def test_runner_reports_readiness_failure_and_still_cleans_up_sitl(self) -> None:
        sitl_process = Mock(pid=90210)
        command_runner = Mock()
        terminate_group = Mock()
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("sitl",),
            process_starter=Mock(return_value=sitl_process),
            readiness_check=Mock(return_value=False),
            terminate_process_group=terminate_group,
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = runner.run(P0_SCENARIOS[:1], Path(temporary_directory))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(command_runner.call_count, 0)
        self.assertEqual(report["overall_status"], "failed")
        self.assertEqual(report["results"][0]["status"], "blocked")
        terminate_group.assert_called_once_with(90210)

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
