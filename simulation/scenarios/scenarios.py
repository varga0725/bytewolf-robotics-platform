"""Run versioned, bounded P0 missions against a disposable headless SITL."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Protocol
from uuid import uuid4

from simulation.gazebo.fault_injection import FaultInjectionError, apply_px4_parameters
from simulation.gazebo.wind_probe import GazeboPoseObserver, expected_hover_tilt_deg
from simulation.gazebo.wind_profiles import (
    SUPPORTED_WIND_SPEEDS_M_S,
    LinearDragModel,
    WindProfileError,
    create_wind_fixture,
    load_linear_drag_model,
)


# The fixture is rendered from PX4's windy world, and PX4 names the model it
# spawns after the airframe and instance.
WIND_WORLD_NAME = "windy"
WIND_MODEL_NAME = "x500_0"


class CompletedProcess(Protocol):
    """The subset of ``subprocess.CompletedProcess`` needed by the runner."""

    returncode: int
    stdout: str
    stderr: str


CommandRunner = Callable[..., CompletedProcess]
ProcessStarter = Callable[..., "ManagedProcess"]
ReadinessCheck = Callable[["ManagedProcess"], bool]
ProcessTerminator = Callable[[int], None]


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
    requires_mavsdk_server: bool = True
    requires_fresh_sitl_lifecycle: bool = False
    # A scenario that claims a wind condition must name the speed it needs, so
    # the runner can build the fixture and the report can prove it was loaded.
    wind_speed_m_s: float | None = None
    # PX4 parameters applied to the booted SITL before the mission runs. This is
    # the only fault injection PX4 actually supports for us; see FAULT_INJECTION
    # in simulation/gazebo/fault_injection.py for what it can and cannot reach.
    px4_parameters: tuple[tuple[str, float], ...] = ()


@dataclass(frozen=True)
class WindEvidence:
    """The exact wind condition a run loaded, recorded so a claim can be audited."""

    speed_m_s: float
    world_file: str
    server_config: str
    models_root: str
    scaling_factor_per_s: float
    extrapolates_drag_model: bool
    airframe_mass_kg: float
    total_mass_kg: float
    verification_level: str = "px4-gazebo-fault-injection"
    # What the vehicle's own attitude proved about the wind it actually flew in.
    # Absent means the wind was handed over but never confirmed.
    observed: dict[str, object] | None = None


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
    # Absent unless the run actually loaded a wind fixture; a wind scenario that
    # reports no evidence here is not a wind run.
    wind: WindEvidence | None = None
    # The PX4 parameters this run injected, as PX4 itself read them back.
    injected_faults: list[dict[str, object]] | None = None


P0_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "takeoff-hover-land",
        "brain.cli.fly_takeoff_hover_land",
        ("--preflight-wait-seconds", "60"),
        fallback_expectation="land-once-after-airborne-failure",
    ),
    Scenario(
        "waypoint-land",
        "brain.cli.fly_waypoint_land",
        ("--preflight-wait-seconds", "60"),
        safety_rejection="must-reject-out-of-bounds-waypoint",
        fallback_expectation="land-once-after-airborne-failure",
    ),
    Scenario(
        "return-to-home",
        "brain.cli.fly_return_to_home",
        ("--preflight-wait-seconds", "60"),
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
        ("--waypoint-timeout", "0.01", "--preflight-wait-seconds", "60"),
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


# P0.v1 remains immutable evidence for the already accepted baseline.  P0.v2
# starts the expanded flight-safety matrix with separate boot/pre-arm evidence
# and the exact nominal takeoff profile requested by the programme.
P0_V2_SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "boot-prearm-check",
        "brain.cli.check_boot_prearm",
        ("--preflight-wait-seconds", "60"),
        version="p0.v2",
        safety_rejection="must-fail-closed-before-arm-if-prearm-is-invalid",
        fallback_expectation="no-flight-command",
        requires_fresh_sitl_lifecycle=True,
    ),
    Scenario(
        "takeoff-2m-hover-10s-land",
        "brain.cli.fly_takeoff_hover_land",
        ("--altitude", "2", "--hover-seconds", "10", "--preflight-wait-seconds", "60"),
        version="p0.v2",
        fallback_expectation="land-once-after-airborne-failure",
        requires_fresh_sitl_lifecycle=True,
    ),
    Scenario(
        "waypoint-square-land",
        "brain.cli.fly_waypoint_square_land",
        (
            "--takeoff-altitude",
            "2",
            "--side-length",
            "5",
            "--waypoint-altitude",
            "2",
            "--hover-seconds",
            "3",
            "--preflight-wait-seconds",
            "60",
        ),
        version="p0.v2",
        fallback_expectation="confirm-four-corners-then-land-once",
        requires_fresh_sitl_lifecycle=True,
    ),
    Scenario(
        "mission-interrupt-hold-cleanup-land",
        "brain.cli.fly_controlled_interruption",
        (
            "--interruption-action",
            "hold",
            "--interrupt-after-seconds",
            "3",
            "--hold-cleanup-seconds",
            "1",
            "--preflight-wait-seconds",
            "60",
        ),
        version="p0.v2",
        fallback_expectation="command-hold-then-cleanup-land-once",
        requires_fresh_sitl_lifecycle=True,
    ),
    Scenario(
        "mission-interrupt-land",
        "brain.cli.fly_controlled_interruption",
        ("--interruption-action", "land", "--interrupt-after-seconds", "3", "--preflight-wait-seconds", "60"),
        version="p0.v2",
        fallback_expectation="command-land-once-after-interrupt",
        requires_fresh_sitl_lifecycle=True,
    ),
    Scenario(
        "reject-geofence-violation",
        "brain.cli.check_geofence_violation",
        version="p0.v2",
        safety_rejection="must-reject-geofence-violation-before-arm",
        fallback_expectation="no-flight-command",
        requires_mavsdk_server=False,
    ),
    Scenario(
        "low-battery-land-fallback",
        "brain.cli.fly_takeoff_hover_land",
        ("--altitude", "2", "--hover-seconds", "20", "--preflight-wait-seconds", "60"),
        version="p0.v2",
        safety_rejection="must-land-once-when-the-live-battery-crosses-the-reserve",
        fallback_expectation="land-once-after-low-battery",
        expected_returncode=1,
        requires_fresh_sitl_lifecycle=True,
        # PX4 drains the battery only while armed and resets it on disarm, so the
        # reserve can only be crossed in flight. A 20 s full-discharge drains
        # 5%/s, passing the 35% reserve well inside the hover.
        px4_parameters=(("SIM_BAT_DRAIN", 20.0), ("SIM_BAT_MIN_PCT", 20.0)),
    ),
    *(
        Scenario(
            f"takeoff-hover-land-wind-{speed:g}ms",
            "brain.cli.fly_takeoff_hover_land",
            ("--altitude", "2", "--hover-seconds", "10", "--preflight-wait-seconds", "60"),
            version="p0.v2",
            fallback_expectation="land-once-after-airborne-failure",
            requires_fresh_sitl_lifecycle=True,
            wind_speed_m_s=speed,
        )
        for speed in SUPPORTED_WIND_SPEEDS_M_S
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
        terminate_process: ProcessTerminator | None = None,
        scenario_timeout_s: float = 120.0,
        startup_wait_s: float = 45.0,
        sitl_start_attempts: int = 2,
        sitl_retry_delay_s: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        px4_root: Path | None = None,
        twin_path: Path | None = None,
        wind_observer: Callable[[str, str, Path], object] = GazeboPoseObserver,
        apply_px4_parameters: Callable[..., tuple] = apply_px4_parameters,
    ) -> None:
        self._wind_observer = wind_observer
        self._apply_px4_parameters = apply_px4_parameters
        self._command_runner = command_runner
        self._uses_default_command_runner = command_runner is subprocess.run
        self._now = now or (lambda: datetime.now(UTC))
        self._python_executable = python_executable
        self._project_root = project_root or Path(__file__).resolve().parents[2]
        self._px4_root = px4_root or self._project_root / "PX4-Autopilot"
        self._twin_path = twin_path or self._project_root / "shared/config/x500v2/twin.yaml"
        self._cached_drag_model: LinearDragModel | None = None
        self.sitl_command = sitl_command
        self._process_starter = process_starter
        self._readiness_check = readiness_check or _process_started
        self._terminate_process = terminate_process or _terminate_process
        self._scenario_timeout_s = scenario_timeout_s
        self._startup_wait_s = startup_wait_s
        self._sitl_start_attempts = sitl_start_attempts
        self._sitl_retry_delay_s = sitl_retry_delay_s
        self._sleep = sleep

    def run(self, scenarios: Iterable[Scenario], output_directory: Path) -> Path:
        """Run every scenario and write one JSON report, including failures."""
        scenario_matrix = tuple(scenarios)
        timestamp = self._now().astimezone(UTC)
        run_output_directory = output_directory / "runs" / (
            f"{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex}"
        )
        if any(scenario.requires_fresh_sitl_lifecycle for scenario in scenario_matrix):
            results = tuple(
                self._run_with_fresh_sitl(scenario, run_output_directory)
                if scenario.requires_fresh_sitl_lifecycle
                else self._run_scenario(scenario, run_output_directory)
                for scenario in scenario_matrix
            )
        else:
            results = self._run_with_shared_sitl(scenario_matrix, run_output_directory)
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

    def _run_with_shared_sitl(
        self, scenarios: tuple[Scenario, ...], output_directory: Path
    ) -> tuple[ScenarioResult, ...]:
        """Preserve the accepted P0.v1 shared-SITL execution contract."""
        sitl_process: ManagedProcess | None = None
        try:
            sitl_process = self._start_sitl()
            if sitl_process is not None:
                self._sleep(self._startup_wait_s)
            if sitl_process is not None and not self._readiness_check(sitl_process):
                return tuple(
                    self._blocked_result(scenario, "SITL readiness check failed.", output_directory)
                    for scenario in scenarios
                )
            return tuple(self._run_scenario(scenario, output_directory) for scenario in scenarios)
        except OSError as error:
            return tuple(
                self._blocked_result(scenario, f"Could not start SITL: {error}.", output_directory)
                for scenario in scenarios
            )
        finally:
            if sitl_process is not None:
                self._stop_sitl(sitl_process)

    def _run_with_fresh_sitl(self, scenario: Scenario, output_directory: Path) -> ScenarioResult:
        """Run one disruptive scenario against a brand-new bounded SITL lifecycle."""
        try:
            environment, wind = self._wind_environment(scenario, output_directory)
        except WindProfileError as error:
            # A wind scenario must never fall back to a still-air run: that would
            # record a wind condition the simulation never applied.
            return self._blocked_result(scenario, f"Could not build the wind fixture: {error}", output_directory)

        sitl_process: ManagedProcess | None = None
        try:
            sitl_process = self._start_sitl(environment)
            if sitl_process is not None:
                self._sleep(self._startup_wait_s)
            if sitl_process is not None and not self._readiness_check(sitl_process):
                return self._blocked_result(scenario, "SITL readiness check failed.", output_directory, wind)
            try:
                injected = self._inject_faults(scenario)
            except FaultInjectionError as error:
                # Running the mission anyway would record a fault it never had.
                return self._blocked_result(scenario, f"Could not inject the fault: {error}", output_directory, wind)
            result = (
                self._run_scenario(scenario, output_directory)
                if wind is None
                else self._run_observed_wind_scenario(scenario, output_directory, wind)
            )
            return result if injected is None else replace(result, injected_faults=injected)
        except OSError as error:
            return self._blocked_result(scenario, f"Could not start SITL: {error}.", output_directory, wind)
        finally:
            if sitl_process is not None:
                self._stop_sitl(sitl_process)

    def _run_observed_wind_scenario(
        self, scenario: Scenario, output_directory: Path, wind: WindEvidence
    ) -> ScenarioResult:
        """Fly the scenario while watching whether the vehicle really feels the wind."""
        expected_tilt = expected_hover_tilt_deg(
            wind.speed_m_s, wind.scaling_factor_per_s, wind.airframe_mass_kg, wind.total_mass_kg
        )
        capture_path = output_directory / "wind-observations" / f"{scenario.identifier}.jsonl"
        with self._wind_observer(WIND_WORLD_NAME, WIND_MODEL_NAME, capture_path) as observer:
            result = self._run_scenario(scenario, output_directory)
        observation = observer.observation(expected_tilt)
        # The raw stream is every entity in the world at ~50 Hz; the verdict is
        # the evidence, and keeping the stream would bloat every run directory.
        capture_path.unlink(missing_ok=True)

        confirmed_wind = replace(wind, observed=asdict(observation))
        if result.status == "passed" and not observation.matches_expected_wind:
            # The mission flew, but not in the wind this run claims. A pass here
            # would be exactly the unprovable wind evidence this check exists for.
            return replace(
                result,
                status="failed",
                stderr=f"{result.stderr}\nWind not confirmed: {observation.detail}",
                wind=confirmed_wind,
            )
        return replace(result, wind=confirmed_wind)

    def _wind_environment(
        self, scenario: Scenario, output_directory: Path
    ) -> tuple[Mapping[str, str] | None, WindEvidence | None]:
        """Build the scenario's wind fixture, or prove it needs none."""
        if scenario.wind_speed_m_s is None:
            return None, None

        fixture_root = output_directory / "wind-fixtures" / f"{scenario.wind_speed_m_s:g}ms"
        px4_worlds = self._px4_root / "Tools/simulation/gz/worlds"
        fixture = create_wind_fixture(
            px4_worlds / "windy.sdf",
            fixture_root / "world.sdf",
            scenario.wind_speed_m_s,
            source_models=self._px4_root / "Tools/simulation/gz/models",
            models_root=fixture_root / "models",
            source_server_config=self._px4_root / "Tools/simulation/gz/server.config",
            output_server_config=fixture_root / "server.config",
            drag_model=self._drag_model(),
        )
        environment = {
            # The fixture is derived from PX4's windy world, so PX4 must wait on
            # and spawn into that world's own name.
            "PX4_GZ_WORLD": "windy",
            "PX4_GZ_WORLD_FILE": str(fixture.output_world),
            "PX4_GZ_MODELS": str(fixture.models_root),
            "PX4_GZ_SERVER_CONFIG": str(fixture.server_config),
        }
        evidence = WindEvidence(
            speed_m_s=fixture.speed_m_s,
            world_file=str(fixture.output_world),
            server_config=str(fixture.server_config),
            models_root=str(fixture.models_root),
            scaling_factor_per_s=fixture.scaling_factor_per_s,
            extrapolates_drag_model=fixture.extrapolates_drag_model,
            airframe_mass_kg=fixture.airframe_mass_kg,
            total_mass_kg=fixture.total_mass_kg,
        )
        return environment, evidence

    def _inject_faults(self, scenario: Scenario) -> list[dict[str, object]] | None:
        """Apply the scenario's PX4 fault parameters and record what PX4 confirmed."""
        if not scenario.px4_parameters:
            return None
        applied = self._apply_px4_parameters(
            scenario.px4_parameters, px4_build_directory=self._px4_root / "build/px4_sitl_default"
        )
        return [asdict(parameter) for parameter in applied]

    def _drag_model(self) -> LinearDragModel:
        if self._cached_drag_model is None:
            self._cached_drag_model = load_linear_drag_model(self._twin_path)
        return self._cached_drag_model

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

    def _run_scenario(
        self, scenario: Scenario, output_directory: Path, wind: WindEvidence | None = None
    ) -> ScenarioResult:
        """Run one scenario, stamping every outcome with the wind it actually loaded."""
        return _with_wind(self._execute_scenario(scenario, output_directory), wind)

    def _execute_scenario(self, scenario: Scenario, output_directory: Path) -> ScenarioResult:
        artifact_directory = self._artifact_directory(output_directory, scenario)
        command = (
            self._python_executable,
            "-m",
            scenario.module,
            *scenario.arguments,
            "--artifact-dir",
            str(artifact_directory),
        )
        if scenario.requires_mavsdk_server:
            command = (
                *command[:-2],
                "--mavsdk-server-port",
                str(_mavsdk_server_port(output_directory, scenario)),
                *command[-2:],
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
        """Run a bounded mission; its CLI shuts down its MAVSDK child in ``finally``."""
        process = subprocess.Popen(
            command,
            cwd=self._project_root,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        deadline = time.monotonic() + self._scenario_timeout_s
        while process.poll() is None and time.monotonic() < deadline:
            artifact = _latest_artifact(artifact_directory)
            if artifact is not None:
                process.terminate()
                stdout, stderr = process.communicate(timeout=5.0)
                return self._result_from_artifact(scenario, command, stdout, stderr, artifact_directory, artifact)
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
            try:
                stdout, stderr = process.communicate(timeout=5.0)
            except subprocess.TimeoutExpired:
                process.kill()
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

    def _start_sitl(self, environment: Mapping[str, str] | None = None) -> ManagedProcess | None:
        if self.sitl_command is None:
            return None
        launch_environment = {**os.environ, **environment} if environment else None
        for attempt in range(self._sitl_start_attempts):
            try:
                return self._process_starter(
                    self.sitl_command,
                    cwd=self._project_root,
                    env=launch_environment,
                    # The process is intentionally long-lived and its output is never
                    # consumed here.  Keeping it in pipes can block PX4 once a pipe
                    # fills, preventing MAVLink from ever becoming available.
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            except OSError:
                if attempt + 1 == self._sitl_start_attempts:
                    raise
                self._sleep(self._sitl_retry_delay_s)
        raise AssertionError("The SITL startup retry loop must return or raise.")

    def _stop_sitl(self, process: ManagedProcess) -> None:
        """Terminate the launcher; its trap reaps both PX4 and Gazebo children."""
        self._terminate_process(process.pid)
        try:
            process.wait(timeout=5.0)
        except subprocess.TimeoutExpired:
            os.kill(process.pid, signal.SIGKILL)
            process.wait(timeout=5.0)

    def _blocked_result(
        self, scenario: Scenario, reason: str, output_directory: Path, wind: WindEvidence | None = None
    ) -> ScenarioResult:
        artifact_directory = self._artifact_directory(output_directory, scenario)
        command = (self._python_executable, "-m", scenario.module, *scenario.arguments)
        return _with_wind(
            self._result_from_process(scenario, command, -1, "", reason, artifact_directory, status="blocked"),
            wind,
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


def _with_wind(result: ScenarioResult, wind: WindEvidence | None) -> ScenarioResult:
    """Attach the loaded wind condition to a result without rebuilding it."""
    return result if wind is None else replace(result, wind=wind)


def _process_started(process: ManagedProcess) -> bool:
    """A default readiness guard: a launch process must remain alive at handoff."""
    return process.pid > 0


def _terminate_process(process_id: int) -> None:
    """Stop the lifecycle-owning launcher without requesting a macOS session."""
    os.kill(process_id, signal.SIGTERM)


def _latest_artifact(directory: Path) -> Path | None:
    """Return the only immutable artifact expected for a bounded CLI invocation."""
    artifacts = tuple(directory.glob("*.json")) if directory.exists() else ()
    return artifacts[0] if len(artifacts) == 1 else None


def _mavsdk_server_port(output_directory: Path, scenario: Scenario) -> int:
    """Derive an isolated local gRPC port from immutable run identity and scenario."""
    identity = f"{output_directory.resolve()}:{scenario.identifier}".encode("utf-8")
    return 51000 + int.from_bytes(sha256(identity).digest()[:2], "big") % 10000


def parse_arguments(arguments: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the headless PX4/Gazebo P0 scenario matrix.")
    parser.add_argument(
        "--matrix-version",
        choices=("p0.v1", "p0.v2"),
        default="p0.v1",
        help="Versioned scenario matrix. p0.v1 remains the accepted baseline.",
    )
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
    scenario_matrix = P0_SCENARIOS if parsed.matrix_version == "p0.v1" else P0_V2_SCENARIOS
    runner = ScenarioRunner(
        sitl_command=("simulation/gazebo/launch/run_px4_gazebo_headless.zsh", "base")
    )
    output_directory = Path("simulation/artifacts/headless")
    if parsed.runs == 1:
        report_path = runner.run(scenario_matrix, output_directory)
    else:
        report_path = runner.run_repeated(
            scenario_matrix,
            output_directory,
            repetitions=parsed.runs,
            minimum_success_rate=parsed.minimum_success_rate,
        )
    print(f"Headless P0 report: {report_path}")


if __name__ == "__main__":
    main()
