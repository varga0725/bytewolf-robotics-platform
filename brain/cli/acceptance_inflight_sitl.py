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
from brain.mission.acceptance_inflight_ports import DEFAULT_MAVLINK_ENDPOINT, mavlink_endpoint_for_gcs
from brain.mission.acceptance_inflight_runner import connect_adapter, run_one_scenario
from brain.mission.acceptance_inflight_sitl import (
    ALL_SCENARIOS,
    OPERATION,
    RELAY_MAX_CAPTURE_AGE_S_DEFAULT,
    check_sitl_failsafe_profile_precondition,
)
from brain.mission.simulation_target import require_simulation_target
from brain.safety.deployment import add_deployment_arguments, actuation_request_sha256, file_sha256
from brain.telemetry.loopback_endpoint import LoopbackEndpointError, require_loopback_udpin_endpoint
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
        default=None,
        help="Scenario to run (repeatable). Default: all when omitted.",
    )
    parser.add_argument("--endpoint", default="udpin://127.0.0.1:14540")
    parser.add_argument(
        "--mavlink-endpoint",
        default=None,
        help="Separate loopback MAVLink URL for pymavlink (default: 14550 when --endpoint is 14540).",
    )
    parser.add_argument(
        "--relay-max-capture-age-s",
        type=float,
        default=RELAY_MAX_CAPTURE_AGE_S_DEFAULT,
        help="Maximum allowed live-telemetry captured_at age for relay-inflight (default 3.0 s).",
    )
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


def _resolve_scenarios(selected: list[str] | None) -> list[str]:
    if not selected:
        return list(ALL_SCENARIOS)
    if "all" in selected:
        return list(ALL_SCENARIOS)
    return list(dict.fromkeys(selected))


def _write_evidence(path: Path, document: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _resolve_mavlink_endpoint(arguments: argparse.Namespace) -> str:
    if arguments.mavlink_endpoint is not None:
        return str(arguments.mavlink_endpoint)
    return mavlink_endpoint_for_gcs(mavsdk_endpoint=arguments.endpoint)


def _resolve_px4_run_directory(arguments: argparse.Namespace) -> Path | None:
    if arguments.px4_run_directory is not None:
        return arguments.px4_run_directory
    env_run = os.environ.get("PX4_RUN_DIR", "").strip()
    return Path(env_run) if env_run else None


async def run(arguments: argparse.Namespace) -> int:
    require_mission_execution_enabled()
    scenarios = _resolve_scenarios(arguments.scenario)
    mavsdk_endpoint = arguments.endpoint
    mavlink_endpoint = _resolve_mavlink_endpoint(arguments)
    try:
        require_loopback_udpin_endpoint(mavlink_endpoint)
    except LoopbackEndpointError as error:
        print(f"mavlink endpoint rejected: {error}", file=sys.stderr)
        return 1

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
        endpoint=mavsdk_endpoint,
        profile=profile,
        operation=OPERATION,
        request_sha256=request_sha256,
        safety_profile_sha256=file_sha256(arguments.safety_profile),
        permit_path=arguments.physical_actuation_permit,
    )

    px4_run = _resolve_px4_run_directory(arguments)
    artifact_dir = arguments.artifact_dir
    artifact_dir.mkdir(parents=True, exist_ok=True)
    summary_path = artifact_dir / "summary.json"
    snapshot_path = artifact_dir / "relay-live-telemetry.json"

    results: list[dict[str, object]] = []
    passed = True

    try:
        for scenario in scenarios:
            system = None
            adapter = None
            try:
                profile_check = check_sitl_failsafe_profile_precondition(
                    px4_run_directory=px4_run,
                    px4_build_directory=arguments.px4_build_directory,
                )
                if profile_check.ok:
                    acquire_px4_link(f"{OPERATION}:{scenario}")
                    system, adapter = await connect_adapter(
                        mavsdk_endpoint,
                        connection_timeout_s=arguments.connection_timeout,
                        preflight_wait_s=arguments.preflight_wait_seconds,
                        mavsdk_server_port=arguments.mavsdk_server_port,
                    )
                document = await run_one_scenario(
                    scenario,
                    adapter,
                    system=system,
                    mavsdk_endpoint=mavsdk_endpoint,
                    mavlink_endpoint=mavlink_endpoint,
                    profile_check=profile_check,
                    snapshot_path=snapshot_path,
                    relay_max_age_s=arguments.relay_max_capture_age_s,
                    px4_run_directory=px4_run,
                    px4_build_directory=arguments.px4_build_directory,
                )
            except Exception as error:
                document = {
                    "version": "acceptance-inflight-sitl.v1",
                    "scenario": scenario,
                    "outcome": "FAIL",
                    "failure_reason": str(error) or error.__class__.__name__,
                    "transitions": [],
                    "timings": {},
                }
            finally:
                stop_owned_mavsdk_server(system)

            evidence_path = artifact_dir / f"{scenario}.json"
            _write_evidence(evidence_path, document)
            outcome = document.get("outcome")
            if outcome != "PASS":
                passed = False
            results.append(
                {
                    "scenario": scenario,
                    "outcome": outcome,
                    "evidence": evidence_path.name,
                    "failure_reason": document.get("failure_reason"),
                }
            )
            print(f"{scenario}: {outcome} -> {evidence_path}")
    finally:
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