"""Run versioned, bounded P0 missions against a disposable headless SITL."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Protocol


class CompletedProcess(Protocol):
    """The subset of ``subprocess.CompletedProcess`` needed by the runner."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CompletedProcess]
ProcessStarter = Callable[..., "ManagedProcess"]
ReadinessCheck = Callable[["ManagedProcess"], bool]
ProcessGroupTerminator = Callable[[int], None]


class ManagedProcess(Protocol):
    """The process operations used to bound the headless SITL lifecycle."""

    pid: int

    def wait(self, timeout: float | None = None) -> int: ...


@dataclass(frozen=True)
class Scenario:
    """A versioned, bounded mission and its testable safety contract."""

    identifier: str
    module: str
    arguments: tuple[str, ...] = ()
    version: str = "p0.v1"
    readiness_requirements: tuple[str, ...] = ("mavsdk-connected", "telemetry-healthy")
    safety_rejection: str | None = "must-not-bypass-safety-gate"
    fallback_expectation: str = "land-once-after-airborne-failure"
    expected_returncode: int = 0


@dataclass(frozen=True)
class ScenarioResult:
    """An immutable record of one scenario invocation."""

    identifier: str
    module: str
    command: tuple[str, ...]
    status: str
    returncode: int
    stdout: str
    stderr: str
    version: str
    readiness_requirements: tuple[str, ...]
    safety_rejection: str | None
    fallback_expectation: str
    expected_returncode: int
    artifact_directory: str


P0_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "takeoff-hover-land",
        "brain.cli.fly_takeoff_hover_land",
        fallback_expectation="land-once-after-airborne-failure",
    ),
    Scenario(
        "waypoint-land",
        "brain.cli.fly_waypoint_land",
        safety_rejection="must-reject-out-of-bounds-waypoint",
        fallback_expectation="land-once-after-airborne-failure",
    ),
    Scenario(
        "return-to-home",
        "brain.cli.fly_return_to_home",
        fallback_expectation="px4-rtl-then-land-once-on-failure",
    ),
    Scenario(
        "reject-unsafe-altitude",
        "brain.cli.fly_takeoff_hover_land",
        ("--altitude", "21"),
        safety_rejection="must-reject-over-max-altitude",
        fallback_expectation="no-flight-command",
        expected_returncode=1,
    ),
    Scenario(
        "waypoint-timeout-fallback",
        "brain.cli.fly_waypoint_land",
        ("--waypoint-timeout", "0.01"),
        safety_rejection="must-record-timeout-and-fallback",
        fallback_expectation="land-once-after-waypoint-timeout",
        expected_returncode=1,
    ),
    Scenario(
        "link-unavailable",
        "brain.cli.fly_takeoff_hover_land",
        ("--endpoint", "udpin://0.0.0.0:14541", "--connection-timeout", "2"),
        safety_rejection="must-fail-closed-before-arm-on-link-loss",
        fallback_expectation="no-flight-command",
        expected_returncode=1,
    ),
)


class ScenarioRunner:
    """Execute a scenario matrix with an optional, bounded headless SITL."""

    def __init__(
        self,
        command_runner: CommandRunner = subprocess.run,
        now: Callable[[], datetime] | None = None,
        python_executable: str = sys.executable,
        project_root: Path | None = None,
        sitl_command: tuple[str, ...] | None = None,
        process_starter: ProcessStarter = subprocess.Popen,
        readiness_check: ReadinessCheck | None = None,
        terminate_process_group: ProcessGroupTerminator | None = None,
        scenario_timeout_s: float = 120.0,
        startup_wait_s: float = 45.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._command_runner = command_runner
        self._uses_default_command_runner = command_runner is subprocess.run
        self._now = now or (lambda: datetime.now(UTC))
        self._python_executable = python_executable
        self._project_root = project_root or Path(__file__).resolve().parents[2]
        self.sitl_command = sitl_command
        self._process_starter = process_starter
        self._readiness_check = readiness_check or _process_started
        self._terminate_process_group = terminate_process_group or _terminate_process_group
        self._scenario_timeout_s = scenario_timeout_s
        self._startup_wait_s = startup_wait_s
        self._sleep = sleep

    def run(self, scenarios: Iterable[Scenario], output_directory: Path) -> Path:
        """Run every scenario and write one JSON report, including failures."""
        scenario_matrix = tuple(scenarios)
        timestamp = self._now().astimezone(UTC)
        sitl_process: ManagedProcess | None = None
        try:
            sitl_process = self._start_sitl()
            if sitl_process is not None:
                self._sleep(self._startup_wait_s)
            if sitl_process is not None and not self._readiness_check(sitl_process):
                results = tuple(
                    self._blocked_result(scenario, "SITL readiness check failed.", output_directory)
                    for scenario in scenario_matrix
                )
            else:
                results = tuple(
                    self._run_scenario(scenario, output_directory) for scenario in scenario_matrix
                )
        except OSError as error:
            results = tuple(
                self._blocked_result(scenario, f"Could not start SITL: {error}.", output_directory)
                for scenario in scenario_matrix
            )
        finally:
            if sitl_process is not None:
                self._stop_sitl(sitl_process)
        output_directory.mkdir(parents=True, exist_ok=True)
        report_path = output_directory / f"p0-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
        report_path.write_text(
            json.dumps(
                {
                    "started_at": timestamp.isoformat(),
                    "overall_status": "passed" if all(result.status == "passed" for result in results) else "failed",
                    "results": [asdict(result) for result in results],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return report_path

    def run_repeated(
        self,
        scenarios: Iterable[Scenario],
        output_directory: Path,
        repetitions: int = 10,
        minimum_success_rate: float = 0.9,
    ) -> Path:
        """Prove nominal P0 scenario repeatability with a durable aggregate report."""
        if repetitions <= 0:
            raise ValueError("Repeatability repetitions must be a positive integer.")
        if not 0.0 < minimum_success_rate <= 1.0:
            raise ValueError("Minimum success rate must be within (0, 1].")

        scenario_matrix = tuple(scenarios)
        timestamp = self._now().astimezone(UTC)
        run_root = output_directory / f"repeatability-{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
        report_paths = tuple(
            self.run(scenario_matrix, run_root / f"run-{index:02d}")
            for index in range(1, repetitions + 1)
        )
        reports = tuple(
            json.loads(path.read_text(encoding="utf-8")) for path in report_paths
        )
        success_rates = {
            scenario.identifier: sum(
                result["status"] == "passed"
                for report in reports
                for result in report["results"]
                if result["identifier"] == scenario.identifier
            )
            / repetitions
            for scenario in scenario_matrix
        }
        nominal_scenarios = tuple(
            scenario.identifier for scenario in scenario_matrix if scenario.expected_returncode == 0
        )
        overall_status = (
            "passed"
            if all(success_rates[identifier] >= minimum_success_rate for identifier in nominal_scenarios)
            and all(
                success_rates[scenario.identifier] == 1.0
                for scenario in scenario_matrix
                if scenario.expected_returncode != 0
            )
            else "failed"
        )
        aggregate_path = output_directory / f"p0-repeatability-{timestamp.strftime('%Y%m%dT%H%M%SZ')}.json"
        output_directory.mkdir(parents=True, exist_ok=True)
        aggregate_path.write_text(
            json.dumps(
                {
                    "minimum_success_rate": minimum_success_rate,
                    "nominal_scenarios": list(nominal_scenarios),
                    "overall_status": overall_status,
                    "repetitions": repetitions,
                    "run_reports": [str(path) for path in report_paths],
                    "started_at": timestamp.isoformat(),
                    "success_rates": success_rates,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return aggregate_path

    def _run_scenario(self, scenario: Scenario, output_directory: Path) -> ScenarioResult:
        artifact_directory = self._artifact_directory(output_directory, scenario)
        command = (
            self._python_executable,
            "-m",
            scenario.module,
            *scenario.arguments,
            "--artifact-dir",
            str(artifact_directory),
        )
        if self._uses_default_command_runner:
            return self._run_isolated_scenario(scenario, command, artifact_directory)
        try:
            completed = self._command_runner(
                command,
                check=False,
                capture_output=True,
                text=True,
                cwd=self._project_root,
                timeout=self._scenario_timeout_s,
            )
        except subprocess.TimeoutExpired as error:
            stdout = error.stdout.decode() if isinstance(error.stdout, bytes) else error.stdout or ""
            stderr = error.stderr.decode() if isinstance(error.stderr, bytes) else error.stderr or ""
            return self._result_from_process(
                scenario, command, -1, stdout, f"{stderr}\nScenario timeout after {self._scenario_timeout_s:g}s.", artifact_directory, "failed"
            )
        return self._result_from_process(
            scenario, command, completed.returncode, completed.stdout, completed.stderr, artifact_directory
        )

    def _run_isolated_scenario(
        self, scenario: Scenario, command: tuple[str, ...], artifact_directory: Path
    ) -> ScenarioResult:
        """Run a mission in its own process group so a timeout cannot leak MAVSDK children."""
        process = subprocess.Popen(
            command,
            cwd=self._project_root,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + self._scenario_timeout_s
        while process.poll() is None and time.monotonic() < deadline:
            artifact = _latest_artifact(artifact_directory)
            if artifact is not None:
                os.killpg(process.pid, signal.SIGTERM)
                stdout, stderr = process.communicate(timeout=5.0)
                return self._result_from_artifact(scenario, command, stdout, stderr, artifact_directory, artifact)
            time.sleep(0.1)
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            return self._result_from_process(
                scenario, command, -1, stdout or "", f"{stderr or ''}\nScenario timeout after {self._scenario_timeout_s:g}s.", artifact_directory, "failed"
            )
        stdout, stderr = process.communicate()
        return self._result_from_process(
            scenario, command, process.returncode, stdout, stderr, artifact_directory
        )

    @staticmethod
    def _result_from_artifact(
        scenario: Scenario,
        command: tuple[str, ...],
        stdout: str,
        stderr: str,
        artifact_directory: Path,
        artifact: Path,
    ) -> ScenarioResult:
        document = json.loads(artifact.read_text(encoding="utf-8"))
        inferred_returncode = 0 if document["outcome"] == "completed" else 1
        return ScenarioRunner._result_from_process(
            scenario, command, inferred_returncode, stdout, stderr, artifact_directory
        )

    def _start_sitl(self) -> ManagedProcess | None:
        if self.sitl_command is None:
            return None
        return self._process_starter(
            self.sitl_command,
            cwd=self._project_root,
            start_new_session=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def _stop_sitl(self, process: ManagedProcess) -> None:
        """Terminate the session leader, then reap it so no simulator is orphaned."""
        self._terminate_process_group(process.pid)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)

    def _blocked_result(self, scenario: Scenario, reason: str, output_directory: Path) -> ScenarioResult:
        artifact_directory = self._artifact_directory(output_directory, scenario)
        command = (self._python_executable, "-m", scenario.module, *scenario.arguments)
        return self._result_from_process(
            scenario, command, -1, "", reason, artifact_directory, status="blocked"
        )

    @staticmethod
    def _artifact_directory(output_directory: Path, scenario: Scenario) -> Path:
        return output_directory / "mission-artifacts" / scenario.identifier

    @staticmethod
    def _result_from_process(
        scenario: Scenario,
        command: tuple[str, ...],
        returncode: int,
        stdout: str,
        stderr: str,
        artifact_directory: Path,
        status: str | None = None,
    ) -> ScenarioResult:
        return ScenarioResult(
            identifier=scenario.identifier,
            module=scenario.module,
            command=command,
            status=status or ("passed" if returncode == scenario.expected_returncode else "failed"),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            version=scenario.version,
            readiness_requirements=scenario.readiness_requirements,
            safety_rejection=scenario.safety_rejection,
            fallback_expectation=scenario.fallback_expectation,
            expected_returncode=scenario.expected_returncode,
            artifact_directory=str(artifact_directory),
        )


def _process_started(process: ManagedProcess) -> bool:
    """A default readiness guard: a launch process must remain alive at handoff."""
    return process.pid > 0


def _terminate_process_group(process_group_id: int) -> None:
    """Stop the isolated SITL session rather than only its shell wrapper."""
    os.killpg(process_group_id, signal.SIGTERM)


def _latest_artifact(directory: Path) -> Path | None:
    """Return the only immutable artifact expected for a bounded CLI invocation."""
    artifacts = tuple(directory.glob("*.json")) if directory.exists() else ()
    return artifacts[0] if len(artifacts) == 1 else None


def parse_arguments(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the headless PX4/Gazebo P0 scenario matrix.")
    parser.add_argument("--runs", type=int, default=1, help="Number of complete P0 matrices to run.")
    parser.add_argument(
        "--minimum-success-rate",
        type=float,
        default=0.9,
        help="Required pass rate for each nominal scenario when --runs is greater than one.",
    )
    return parser.parse_args(arguments)


def main(arguments: Iterable[str] | None = None) -> None:
    """Launch an isolated headless PX4 SITL and run the standard P0 matrix."""
    parsed = parse_arguments(arguments)
    runner = ScenarioRunner(
        sitl_command=("simulation/launch/run_px4_gazebo_headless.zsh", "base")
    )
    output_directory = Path("simulation/artifacts/headless")
    if parsed.runs == 1:
        report_path = runner.run(P0_SCENARIOS, output_directory)
    else:
        report_path = runner.run_repeated(
            P0_SCENARIOS,
            output_directory,
            repetitions=parsed.runs,
            minimum_success_rate=parsed.minimum_success_rate,
        )
    print(f"Headless P0 report: {report_path}")


if __name__ == "__main__":
    main()
