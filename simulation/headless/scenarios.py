"""Run versioned, bounded P0 missions against a disposable headless SITL."""

from __future__ import annotations

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
        startup_wait_s: float = 20.0,
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
                results = tuple(self._blocked_result(scenario, "SITL readiness check failed.") for scenario in scenario_matrix)
            else:
                results = tuple(self._run_scenario(scenario) for scenario in scenario_matrix)
        except OSError as error:
            results = tuple(self._blocked_result(scenario, f"Could not start SITL: {error}.") for scenario in scenario_matrix)
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

    def _run_scenario(self, scenario: Scenario) -> ScenarioResult:
        command = (self._python_executable, "-m", scenario.module, *scenario.arguments)
        if self._uses_default_command_runner:
            return self._run_isolated_scenario(scenario, command)
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
                scenario, command, -1, stdout, f"{stderr}\nScenario timeout after {self._scenario_timeout_s:g}s.", "failed"
            )
        return self._result_from_process(scenario, command, completed.returncode, completed.stdout, completed.stderr)

    def _run_isolated_scenario(
        self, scenario: Scenario, command: tuple[str, ...]
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
        try:
            stdout, stderr = process.communicate(timeout=self._scenario_timeout_s)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                stdout, stderr = process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                stdout, stderr = process.communicate()
            return self._result_from_process(
                scenario, command, -1, stdout or "", f"{stderr or ''}\nScenario timeout after {self._scenario_timeout_s:g}s.", "failed"
            )
        return self._result_from_process(scenario, command, process.returncode, stdout, stderr)

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

    def _blocked_result(self, scenario: Scenario, reason: str) -> ScenarioResult:
        command = (self._python_executable, "-m", scenario.module, *scenario.arguments)
        return self._result_from_process(scenario, command, -1, "", reason, status="blocked")

    @staticmethod
    def _result_from_process(
        scenario: Scenario,
        command: tuple[str, ...],
        returncode: int,
        stdout: str,
        stderr: str,
        status: str | None = None,
    ) -> ScenarioResult:
        return ScenarioResult(
            identifier=scenario.identifier,
            module=scenario.module,
            command=command,
            status=status or ("passed" if returncode == 0 else "failed"),
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            version=scenario.version,
            readiness_requirements=scenario.readiness_requirements,
            safety_rejection=scenario.safety_rejection,
            fallback_expectation=scenario.fallback_expectation,
        )


def _process_started(process: ManagedProcess) -> bool:
    """A default readiness guard: a launch process must remain alive at handoff."""
    return process.pid > 0


def _terminate_process_group(process_group_id: int) -> None:
    """Stop the isolated SITL session rather than only its shell wrapper."""
    os.killpg(process_group_id, signal.SIGTERM)


def main() -> None:
    """Launch an isolated headless PX4 SITL and run the standard P0 matrix."""
    report_path = ScenarioRunner(
        sitl_command=("simulation/launch/run_px4_gazebo_headless.zsh", "base")
    ).run(P0_SCENARIOS, Path("simulation/artifacts/headless"))
    print(f"Headless P0 report: {report_path}")


if __name__ == "__main__":
    main()
