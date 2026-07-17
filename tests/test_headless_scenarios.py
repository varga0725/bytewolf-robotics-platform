"""Unit tests for the manual headless P0 scenario runner."""

from __future__ import annotations

from dataclasses import replace
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


def _write_px4_wind_sources(root: Path) -> Path:
    """Write the smallest PX4 tree a wind fixture can be rendered from."""
    worlds = root / "Tools/simulation/gz/worlds"
    worlds.mkdir(parents=True)
    (worlds / "windy.sdf").write_text(
        "<sdf><world name='windy'><wind><linear_velocity>5 2 0</linear_velocity></wind></world></sdf>",
        encoding="utf-8",
    )
    models = root / "Tools/simulation/gz/models"
    (models / "x500_base").mkdir(parents=True)
    (models / "x500_base" / "model.sdf").write_text(
        "<sdf><model name='x500_base'><link name=\"base_link\"><inertial><mass>2.0</mass></inertial>"
        "</link></model></sdf>",
        encoding="utf-8",
    )
    (models / "x500").mkdir(parents=True)
    (models / "x500" / "model.sdf").write_text(
        "<sdf><model name='x500'><include merge='true'><uri>model://x500_base</uri></include></model></sdf>",
        encoding="utf-8",
    )
    (root / "Tools/simulation/gz/server.config").write_text(
        "<server_config>\n  <plugins>\n  </plugins>\n</server_config>\n", encoding="utf-8"
    )
    return root


def _write_twin(root: Path) -> Path:
    twin = root / "twin.yaml"
    twin.write_text(
        "aerodynamics:\n"
        "  linear_drag_coefficient_kg_s: 0.285\n"
        "  linear_drag_valid_airspeed_m_s: [2.0, 9.0]\n",
        encoding="utf-8",
    )
    return twin


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
        self.assertIn("waypoint-square-land", tuple(scenario.identifier for scenario in P0_V2_SCENARIOS))

    def test_p0_v2_covers_each_required_wind_speed_with_a_fresh_lifecycle(self) -> None:
        wind_scenarios = tuple(scenario for scenario in P0_V2_SCENARIOS if scenario.wind_speed_m_s is not None)

        self.assertEqual(
            tuple(scenario.identifier for scenario in wind_scenarios),
            ("takeoff-hover-land-wind-3ms", "takeoff-hover-land-wind-6ms", "takeoff-hover-land-wind-10ms"),
        )
        self.assertEqual(tuple(scenario.wind_speed_m_s for scenario in wind_scenarios), (3.0, 6.0, 10.0))
        # Wind changes the world, so it cannot share a lifecycle with a still-air run.
        self.assertTrue(all(scenario.requires_fresh_sitl_lifecycle for scenario in wind_scenarios))
        self.assertTrue(all(scenario.version == "p0.v2" for scenario in wind_scenarios))

    def test_only_wind_scenarios_declare_a_wind_speed(self) -> None:
        self.assertTrue(all(scenario.wind_speed_m_s is None for scenario in P0_SCENARIOS))


class WindScenarioEvidenceTests(unittest.TestCase):
    """A wind run's report has to prove which wind the simulation actually loaded."""

    _WIND_SCENARIO = Scenario(
        "takeoff-hover-land-wind-6ms",
        "brain.cli.fly_takeoff_hover_land",
        version="p0.v2",
        requires_fresh_sitl_lifecycle=True,
        wind_speed_m_s=6.0,
    )

    def _runner(self, root: Path, **overrides: object) -> ScenarioRunner:
        defaults: dict[str, object] = {
            "command_runner": Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
            "now": lambda: datetime(2026, 7, 17, 8, 0, tzinfo=UTC),
            "process_starter": Mock(return_value=Mock(pid=4242)),
            "terminate_process": Mock(),
            "sitl_command": ("launcher",),
            "sleep": Mock(),
            "px4_root": _write_px4_wind_sources(root / "px4"),
            "twin_path": _write_twin(root),
        }
        return ScenarioRunner(**{**defaults, **overrides})

    def test_records_the_exact_fixture_the_run_loaded(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self._runner(root)

            report_path = runner.run((self._WIND_SCENARIO,), output_directory=root / "out")
            wind = json.loads(report_path.read_text(encoding="utf-8"))["results"][0]["wind"]

            self.assertEqual(wind["speed_m_s"], 6.0)
            self.assertEqual(wind["verification_level"], "px4-gazebo-fault-injection")
            self.assertAlmostEqual(wind["scaling_factor_per_s"], 0.1425)
            self.assertFalse(wind["extrapolates_drag_model"])
            self.assertIn("6ms", wind["world_file"])
            self.assertIn("<linear_velocity>6 0 0</linear_velocity>", Path(wind["world_file"]).read_text())
            self.assertIn("WindEffects", Path(wind["server_config"]).read_text())

    def test_hands_the_fixture_to_the_launcher(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            starter = Mock(return_value=Mock(pid=4242))
            runner = self._runner(root, process_starter=starter)

            runner.run((self._WIND_SCENARIO,), output_directory=root / "out")
            environment = starter.call_args.kwargs["env"]

            self.assertEqual(environment["PX4_GZ_WORLD"], "windy")
            self.assertIn("6ms", environment["PX4_GZ_WORLD_FILE"])
            self.assertIn("6ms", environment["PX4_GZ_MODELS"])
            self.assertIn("6ms", environment["PX4_GZ_SERVER_CONFIG"])

    def test_a_still_air_scenario_neither_builds_a_fixture_nor_claims_wind(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            starter = Mock(return_value=Mock(pid=4242))
            runner = self._runner(root, process_starter=starter)

            report_path = runner.run((P0_V2_SCENARIOS[1],), output_directory=root / "out")
            result = json.loads(report_path.read_text(encoding="utf-8"))["results"][0]

            self.assertIsNone(result["wind"])
            self.assertIsNone(starter.call_args.kwargs["env"])

    def test_blocks_a_wind_run_whose_fixture_cannot_be_built(self) -> None:
        """A wind scenario must never silently fall back to a still-air run."""
        with TemporaryDirectory() as directory:
            root = Path(directory)
            command_runner = Mock(return_value=Mock(returncode=0, stdout="", stderr=""))
            unknown_drag_twin = root / "twin-without-drag.yaml"
            unknown_drag_twin.write_text("aerodynamics:\n  linear_drag_coefficient_kg_s: null\n", encoding="utf-8")
            runner = self._runner(root, twin_path=unknown_drag_twin, command_runner=command_runner)

            report_path = runner.run((self._WIND_SCENARIO,), output_directory=root / "out")
            result = json.loads(report_path.read_text(encoding="utf-8"))["results"][0]

            self.assertEqual(result["status"], "blocked")
            self.assertIn("wind fixture", result["stderr"])
            self.assertIsNone(result["wind"])
            command_runner.assert_not_called()

    def test_marks_a_wind_speed_outside_the_backed_drag_band(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            runner = self._runner(root)
            scenario = replace(self._WIND_SCENARIO, identifier="wind-10ms", wind_speed_m_s=10.0)

            report_path = runner.run((scenario,), output_directory=root / "out")
            wind = json.loads(report_path.read_text(encoding="utf-8"))["results"][0]["wind"]

            self.assertTrue(wind["extrapolates_drag_model"])

    def test_runner_can_select_the_expanded_p0_v2_matrix_without_replacing_v1(self) -> None:
        self.assertEqual(parse_arguments(()).matrix_version, "p0.v1")
        self.assertEqual(parse_arguments(("--matrix-version", "p0.v2")).matrix_version, "p0.v2")
        self.assertEqual(P0_V2_SCENARIOS[0].module, "brain.cli.check_boot_prearm")
        self.assertEqual(P0_V2_SCENARIOS[0].arguments, ("--preflight-wait-seconds", "60"))
        self.assertEqual(
            P0_V2_SCENARIOS[1].arguments,
            ("--altitude", "2", "--hover-seconds", "10", "--preflight-wait-seconds", "60"),
        )
        self.assertIn(
            "reject-geofence-violation",
            tuple(scenario.identifier for scenario in P0_V2_SCENARIOS),
        )
        geofence_scenario = next(
            scenario for scenario in P0_V2_SCENARIOS if scenario.identifier == "reject-geofence-violation"
        )
        self.assertEqual(geofence_scenario.module, "brain.cli.check_geofence_violation")
        self.assertEqual(geofence_scenario.fallback_expectation, "no-flight-command")
        self.assertFalse(geofence_scenario.requires_fresh_sitl_lifecycle)
        self.assertTrue(
            all(
                scenario.requires_fresh_sitl_lifecycle
                for scenario in P0_V2_SCENARIOS
                if scenario.requires_mavsdk_server
            )
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

    def test_p0_v2_runs_each_mavsdk_scenario_with_a_fresh_sitl_lifecycle(self) -> None:
        """Disruptive P0.v2 missions must not inherit a previous vehicle state."""
        first_process = Mock(pid=90210)
        second_process = Mock(pid=90211)
        process_starter = Mock(side_effect=(first_process, second_process))
        command_runner = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        terminate_process = Mock()
        scenarios = (
            Scenario("first", "brain.cli.first", version="p0.v2", requires_fresh_sitl_lifecycle=True),
            Scenario("local-check", "brain.cli.local", version="p0.v2", requires_mavsdk_server=False),
            Scenario("second", "brain.cli.second", version="p0.v2", requires_fresh_sitl_lifecycle=True),
        )
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("sitl",),
            process_starter=process_starter,
            readiness_check=Mock(return_value=True),
            terminate_process=terminate_process,
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = runner.run(scenarios, Path(temporary_directory))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual(process_starter.call_count, 2)
        self.assertEqual(terminate_process.call_args_list, [((90210,),), ((90211,),)])
        self.assertEqual(command_runner.call_count, 3)
        self.assertEqual(report["overall_status"], "passed")

    def test_p0_v2_fresh_sitl_start_failure_blocks_only_its_scenario(self) -> None:
        """One failed disposable SITL must not hide independent scenario evidence."""
        working_process = Mock(pid=90211)
        process_starter = Mock(side_effect=(OSError("launcher unavailable"), OSError("launcher unavailable"), working_process))
        command_runner = Mock(return_value=Mock(returncode=0, stdout="ok", stderr=""))
        scenarios = (
            Scenario("unavailable", "brain.cli.first", version="p0.v2", requires_fresh_sitl_lifecycle=True),
            Scenario("available", "brain.cli.second", version="p0.v2", requires_fresh_sitl_lifecycle=True),
        )
        runner = ScenarioRunner(
            command_runner=command_runner,
            sitl_command=("sitl",),
            process_starter=process_starter,
            readiness_check=Mock(return_value=True),
            terminate_process=Mock(),
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            report_path = runner.run(scenarios, Path(temporary_directory))
            report = json.loads(report_path.read_text(encoding="utf-8"))

        self.assertEqual([result["status"] for result in report["results"]], ["blocked", "passed"])
        self.assertEqual(command_runner.call_count, 1)

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
