"""Scenario contracts must not pass on connect timeouts alone."""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from simulation.scenarios.scenario_contract import scenario_contract_violation
from simulation.scenarios.sitl_startup import format_sitl_launch_failed


class ScenarioContractTests(unittest.TestCase):
    def test_waypoint_timeout_rejects_connect_only_failure(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "run.json").write_text(
                json.dumps(
                    {
                        "events": [],
                        "outcome": "failed",
                        "failure_reason": "TimeoutError: ",
                    }
                ),
                encoding="utf-8",
            )
            violation = scenario_contract_violation(
                scenario_identifier="waypoint-timeout-fallback",
                fallback_expectation="land-once-after-waypoint-timeout",
                stdout="",
                stderr="Connection timed out",
                artifact_directory=directory,
            )

        self.assertIsNotNone(violation)

    def test_waypoint_timeout_accepts_in_flight_fallback_artifact(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "run.json").write_text(
                json.dumps(
                    {
                        "events": [
                            {"phase": "arming"},
                            {"phase": "taking_off"},
                            {"phase": "navigating"},
                            {"phase": "landing"},
                            {"phase": "failed"},
                        ],
                        "outcome": "failed",
                        "failure_reason": "TimeoutError: ",
                    }
                ),
                encoding="utf-8",
            )
            violation = scenario_contract_violation(
                scenario_identifier="waypoint-timeout-fallback",
                fallback_expectation="land-once-after-waypoint-timeout",
                stdout="",
                stderr="",
                artifact_directory=directory,
            )

        self.assertIsNone(violation)

    def test_takeoff_hover_rejects_low_peak_altitude(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            (directory / "run.json").write_text(
                json.dumps(
                    {
                        "events": [{"phase": "arming"}, {"phase": "completed"}],
                        "outcome": "completed",
                        "peak_relative_altitude_m": 0.5,
                    }
                ),
                encoding="utf-8",
            )
            violation = scenario_contract_violation(
                scenario_identifier="takeoff-hover-land",
                fallback_expectation="land-once-after-airborne-failure",
                stdout="",
                stderr="",
                artifact_directory=directory,
            )

        self.assertIn("peak relative altitude", violation or "")

    def test_waypoint_timeout_rejects_sitl_launch_failure(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            violation = scenario_contract_violation(
                scenario_identifier="waypoint-timeout-fallback",
                fallback_expectation="land-once-after-waypoint-timeout",
                stdout="",
                stderr=format_sitl_launch_failed(2, "qt missing"),
                artifact_directory=Path(temporary_directory),
            )

        self.assertIn("launcher exited", violation or "")


if __name__ == "__main__":
    unittest.main()
