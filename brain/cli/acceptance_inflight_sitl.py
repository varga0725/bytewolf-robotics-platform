"""In-flight SITL failsafe acceptance harness (BYT-66)."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server
from brain.mission.acceptance_inflight_runner import (
    connect_adapter,
    disconnect_adapter,
    run_one_scenario,
)
from brain.mission.acceptance_inflight_sitl import ALL_SCENARIOS, OPERATION, check_sitl_failsafe_profile_precondition
from brain.mission.simulation_target import require_simulation_target
from brain.safety.deployment import add_deployment_arguments, actuation_request_sha256, file_sha256
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from shared.safety.execution_gate import require_mission_execution_enabled
from simulation.gazebo.fault_injection import PX4_BUILD_DIRECTORY


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run in-flight SITL failsafe acceptance scenarios and write per-scenario evidence JSON.",
    )
    parser.add_argument(
        "--scenario",
        action="append",
        choices=[*ALL_SCENARIOS, "all"],
        default=["all"],
        help="Scenario to run (repeatable). Default: all.",
    )
    parser.add_argument("--endpoint", default="udpin://127.0.0.1:14540")
    parser.add_argument("--connection-timeout", type=float, default=30.0)
    parser.add_argument("--preflight-wait-seconds", type=float, default=120.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument(
        "--artifact-dir",
        type=Path,
        default=Path("simulation/artifacts/acceptance-inflight-sitl"),
    )
    parser.add_argument(
        "--px4-run-directory",
        type=Path,
        default=None,
        help="PX4 disposable run directory (default: PX4_RUN_DIR environment variable).",
    )
    parser.add_argument(
        "--px4-build-directory",
        type=Path,
        default=PX4_BUILD_DIRECTORY,
    )
    parser.add_argument(
        "--safety-profile",
        type=Path,
        default=DEFAULT_SAFETY_PROFILE_PATH,
    )
    add_deployment_arguments(parser, actuation_capable=True)
    return parser.parse_args(arguments)


def _resolve_scenarios(selected: list[str]) -> list[str]:
    if "all" in selected:
        return list(ALL_SCENARIOS)
    return list(dict.fromkeys(selected))


def _write_evidence(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def run(arguments: argparse.Namespace) -> int:
    require_mission_execution_enabled()
    scenarios = _resolve_scenarios(list(arguments.scenario))
    profile = load_safety_profile(arguments.safety_profile)
    request_sha256 = actuation_request_sha256(
        {
            "operation": OPERATION,
            "vehicle_id": profile.vehicle_id,
            "scenarios": scenarios,
        }
    )
    require_simulation_target(
        deployment_mode=arguments.deployment_mode,
        endpoint=arguments.endpoint,
        profile=profile,
        operation=OPERATION,
        request_sha256=request_sha256,
        safety_profile_sha256=file_sha256(arguments.safety_profile),
        permit_path=arguments.physical_actuation_permit,
    )

    px4_run = arguments.px4_run_directory
    if px4_run is None:
        env_run = os.environ.get("PX4_RUN_DIR", "").strip()
        if env_run:
            px4_run = Path(env_run)

    profile_check = check_sitl_failsafe_profile_precondition(
        px4_run_directory=px4_run,
        px4_build_directory=arguments.px4_build_directory,
    )

    artifact_dir = arguments.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / "summary.json"
    snapshot_path = artifact_dir / "relay-live-telemetry.json"

    results: list[dict[str, object]] = []
    system = None
    adapter = None

    try:
        if profile_check.ok:
            acquire_px4_link(OPERATION)
            system, adapter = await connect_adapter(
                arguments.endpoint,
                connection_timeout_s=arguments.connection_timeout,
                preflight_wait_s=arguments.preflight_wait_seconds,
                mavsdk_server_port=arguments.mavsdk_server_port,
            )

        for scenario in scenarios:
            document = await run_one_scenario(
                scenario,
                adapter,
                endpoint=arguments.endpoint,
                profile_check=profile_check,
                snapshot_path=snapshot_path,
            )
            evidence_path = artifact_dir / f"{scenario}.json"
            _write_evidence(evidence_path, document)
            results.append(
                {
                    "scenario": scenario,
                    "outcome": document.get("outcome"),
                    "evidence": str(evidence_path),
                    "failure_reason": document.get("failure_reason"),
                }
            )
            print(f"{scenario}: {document.get('outcome')} -> {evidence_path}")
    finally:
        if system is not None:
            await disconnect_adapter(system)
        stop_owned_mavsdk_server()

    passed = all(item.get("outcome") == "PASS" for item in results)
    summary = {
        "recorded_at": datetime.now(UTC).isoformat(),
        "operation": OPERATION,
        "scenarios": results,
        "outcome": "PASS" if passed else "FAIL",
    }
    _write_evidence(summary_path, summary)
    return 0 if passed else 1


def main(arguments: Sequence[str] | None = None) -> int:
    try:
        return asyncio.run(run(parse_arguments(arguments)))
    except KeyboardInterrupt:
        return 130


def run_unit_tests(argv: list[str] | None = None) -> int:
    """Discover mocked unit tests (CI default)."""
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    import unittest

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_acceptance_inflight_sitl")
    if suite.countTestCases() == 0:
        suite = loader.discover(
            start_dir=str(repository_root / "tests"),
            pattern="test_acceptance_inflight_sitl.py",
            top_level_dir=str(repository_root),
        )
    if suite.countTestCases() == 0:
        print("No in-flight SITL acceptance tests were discovered.", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    if os.environ.get("BYTEWOLF_ACCEPTANCE_INFLIGHT_UNITTEST") == "1":
        raise SystemExit(run_unit_tests())
    raise SystemExit(main())