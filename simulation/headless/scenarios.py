"""Run bounded manual P0 missions and retain a machine-readable result."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import json
from pathlib import Path
import subprocess
import sys
from typing import Protocol


class CompletedProcess(Protocol):
    """The subset of ``subprocess.CompletedProcess`` needed by the runner."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CompletedProcess]


@dataclass(frozen=True)
class Scenario:
    """A bounded CLI mission suitable for a manually-started PX4 SITL."""

    identifier: str
    module: str
    arguments: tuple[str, ...] = ()


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


P0_SCENARIOS: tuple[Scenario, ...] = (
    Scenario("takeoff-hover-land", "brain.cli.fly_takeoff_hover_land"),
    Scenario("waypoint-land", "brain.cli.fly_waypoint_land"),
    Scenario("return-to-home", "brain.cli.fly_return_to_home"),
)


class ScenarioRunner:
    """Execute a scenario matrix only after a headless PX4 SITL is available."""

    def __init__(
        self,
        command_runner: CommandRunner = subprocess.run,
        now: Callable[[], datetime] | None = None,
        python_executable: str = sys.executable,
        project_root: Path | None = None,
    ) -> None:
        self._command_runner = command_runner
        self._now = now or (lambda: datetime.now(UTC))
        self._python_executable = python_executable
        self._project_root = project_root or Path(__file__).resolve().parents[2]

    def run(self, scenarios: Iterable[Scenario], output_directory: Path) -> Path:
        """Run every scenario and write one JSON report, including failures."""
        results = tuple(self._run_scenario(scenario) for scenario in scenarios)
        timestamp = self._now().astimezone(UTC)
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
        completed = self._command_runner(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=self._project_root,
        )
        return ScenarioResult(
            identifier=scenario.identifier,
            module=scenario.module,
            command=command,
            status="passed" if completed.returncode == 0 else "failed",
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def main() -> None:
    """Run the standard P0 matrix against an already-running PX4 SITL instance."""
    report_path = ScenarioRunner().run(P0_SCENARIOS, Path("simulation/artifacts/headless"))
    print(f"Headless P0 report: {report_path}")


if __name__ == "__main__":
    main()
