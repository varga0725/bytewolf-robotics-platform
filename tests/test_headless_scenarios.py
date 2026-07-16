"""Unit tests for the manual headless P0 scenario runner."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from simulation.headless.scenarios import P0_SCENARIOS, ScenarioRunner


class HeadlessScenarioTests(unittest.TestCase):
    def test_p0_matrix_covers_the_bounded_flight_commands(self) -> None:
        self.assertEqual(
            tuple(scenario.identifier for scenario in P0_SCENARIOS),
            ("takeoff-hover-land", "waypoint-land", "return-to-home"),
        )
        self.assertTrue(all(scenario.module.startswith("brain.cli.") for scenario in P0_SCENARIOS))

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


if __name__ == "__main__":
    unittest.main()
