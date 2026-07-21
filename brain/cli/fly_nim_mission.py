"""Control PX4 SITL through a NIM Mission Agent and deterministic safety gates."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path
from uuid import uuid4

from apps.gateway.nim_mission_agent import MissionAgentRequest, NIMMissionAgent
from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.artifacts import recorded_execution, write_run_artifact
from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server
from brain.memory.recorder import DEFAULT_WORLD_MEMORY_PATH, WorldMemoryRecorder
from brain.mission.execution import MissionExecution
from brain.mission_spec.orchestrator import execute_compiled_mission, require_executable_mission
from brain.mission_spec.reviewed_plan import (
    default_plan_path as _default_plan_path,
    require_matching_review_approval as _require_matching_review_approval,
    write_reviewed_plan as _write_reviewed_plan,
)
from brain.mission_spec.validation import (
    CompiledMission,
    MissionSafetyProfile,
    load_mission_safety_profile,
    validate_and_compile_mission_spec,
)
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Ask NVIDIA NIM for a MissionSpec proposal, then optionally run it on PX4 SITL."
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--command", help="Natural-language mission request sent to the NIM Mission Agent for review.")
    source.add_argument("--mission-spec-file", type=Path, help="Previously reviewed normalized MissionSpec to execute exactly.")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly connect to PX4 SITL and execute an approved proposal. Without it, only validate and print.",
    )
    parser.add_argument(
        "--plan-file",
        type=Path,
        default=None,
        help="Where a reviewed NIM MissionSpec is saved. Defaults under the ignored agent artifact directory.",
    )
    parser.add_argument("--safety-profile", type=Path, default=DEFAULT_SAFETY_PROFILE_PATH)
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=15.0)
    parser.add_argument("--preflight-wait-seconds", type=float, default=120.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument("--artifact-dir", type=Path, default=None)
    parser.add_argument(
        "--dashboard-snapshot",
        type=Path,
        default=Path("simulation/artifacts/dashboard/live-telemetry.json"),
    )
    parser.add_argument(
        "--world-memory-file",
        type=Path,
        default=DEFAULT_WORLD_MEMORY_PATH,
        help="Where this run's outcome is remembered as perishable world evidence.",
    )
    parser.add_argument(
        "--no-world-memory",
        action="store_true",
        help="Run without remembering the outcome; the mission audit artifact is unaffected.",
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    mission_profile = load_mission_safety_profile(arguments.safety_profile)
    execution = MissionExecution.empty()
    system = None
    adapter: MavsdkMissionAdapter | None = None
    safety_decision = "not-evaluated"
    outcome = "failed"
    failure_reason: str | None = None
    relay_stop: asyncio.Event | None = None
    relay_task: asyncio.Task[None] | None = None
    mission_spec: dict[str, object] | None = None
    mission: CompiledMission | None = None
    model = "not-called"
    plan_path: Path | None = None
    try:
        if arguments.command is not None:
            if arguments.execute:
                raise RuntimeError("Review first, then run that exact file with --mission-spec-file ... --execute.")
            result = NIMMissionAgent.from_environment().propose(
                MissionAgentRequest(arguments.command, mission_profile)
            )
            model = result.model
            if not result.accepted or result.mission is None or result.mission_spec is None:
                safety_decision = "rejected"
                details = "; ".join(
                    f"{item.constraint or 'mission'}: {item.reason}" for item in result.rejections
                )
                raise RuntimeError(f"NIM Mission Agent refused the command. {details}")
            mission_spec, mission = result.mission_spec, result.mission
            plan_path = arguments.plan_file or _default_plan_path(mission.mission_id)
            _write_reviewed_plan(plan_path, mission_spec, model)
            safety_decision = "approved"
            outcome = "validated"
            steps = " → ".join(str(step["type"]) for step in mission_spec["steps"])
            print(f"NIM model {model} proposed and safety-approved: {steps}")
            print(f"Mission ID: {mission.mission_id}")
            print(f"Reviewed plan: {plan_path}")
            print("Execute this exact plan with --mission-spec-file <path> --execute.")
            return

        plan_path = arguments.mission_spec_file
        assert plan_path is not None
        mission_spec, mission = _load_approved_plan(plan_path, mission_profile)
        model = "reviewed-plan"
        safety_decision = "approved"
        if not arguments.execute:
            outcome = "validated"
            print(f"Reviewed plan is executable: {plan_path}")
            print("Add --execute to connect to PX4 SITL.")
            return

        assert mission is not None
        try:
            from mavsdk import System
        except ModuleNotFoundError as error:
            raise RuntimeError("MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt") from error
        profile = load_safety_profile(arguments.safety_profile)
        system = System(port=arguments.mavsdk_server_port)
        adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=arguments.preflight_wait_seconds)
        print(f"Connecting to PX4 SITL at {arguments.endpoint}...")
        # Take the endpoint before MAVSDK binds it; the bridge yields to this.
        acquire_px4_link("agent-mission")
        await asyncio.wait_for(adapter.connect(arguments.endpoint), timeout=arguments.connection_timeout)
        relay_stop = asyncio.Event()
        relay_task = asyncio.create_task(MavsdkTelemetryRelay(system, arguments.dashboard_snapshot).run(relay_stop))
        execution = await execute_compiled_mission(adapter, mission)
        outcome = "completed"
        print("Mission completed: " + " -> ".join(event.phase.value for event in execution.events))
    except Exception as error:
        if safety_decision == "not-evaluated":
            safety_decision = "rejected"
        failure_reason = f"{type(error).__name__}: {error}"
        execution = recorded_execution(adapter, execution)
        raise
    finally:
        if relay_stop is not None:
            relay_stop.set()
        if relay_task is not None:
            try:
                await relay_task
            except Exception as error:
                print(f"Dashboard telemetry relay stopped: {type(error).__name__}: {error}")
        stop_owned_mavsdk_server(system)
        _write_agent_decision(
            arguments.artifact_dir,
            command=arguments.command,
            model=model,
            mission_spec=mission_spec,
            plan_path=plan_path,
            safety_decision=safety_decision,
            outcome=outcome,
            failure_reason=failure_reason,
        )
        if arguments.execute and mission is not None:
            write_run_artifact(
                arguments.artifact_dir,
                execution,
                safety_decision,
                outcome,
                failure_reason,
                getattr(adapter, "preflight_telemetry", None),
                world_recorder=_world_recorder(arguments),
            )


def _world_recorder(arguments: argparse.Namespace) -> WorldMemoryRecorder | None:
    """Remember the outcome unless the operator opted out for this run."""
    if arguments.no_world_memory:
        return None
    return WorldMemoryRecorder(arguments.world_memory_file)


def _load_approved_plan(
    path: Path, profile: MissionSafetyProfile
) -> tuple[dict[str, object], CompiledMission]:
    try:
        raw_document = path.read_bytes()
    except OSError as error:
        raise RuntimeError(f"Cannot read reviewed MissionSpec '{path}': {error.strerror}.") from error
    _require_matching_review_approval(path, raw_document)
    try:
        document = json.loads(raw_document)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Reviewed MissionSpec '{path}' is not JSON.") from error
    if not isinstance(document, dict):
        raise RuntimeError("Reviewed MissionSpec must be a JSON object.")
    report = validate_and_compile_mission_spec(document, profile)
    if not report.approved or report.mission is None:
        details = "; ".join(issue.message for issue in report.issues)
        raise RuntimeError(f"Reviewed MissionSpec is no longer approved: {details}")
    require_executable_mission(report.mission)
    return document, report.mission


def _write_agent_decision(
    artifact_dir: Path | None,
    *,
    command: str | None,
    model: str,
    mission_spec: dict[str, object] | None,
    plan_path: Path | None,
    safety_decision: str,
    outcome: str,
    failure_reason: str | None,
) -> None:
    directory = artifact_dir or Path("simulation/artifacts/agent-missions")
    directory.mkdir(parents=True, exist_ok=True)
    canonical_spec = json.dumps(mission_spec, sort_keys=True, separators=(",", ":")) if mission_spec else None
    record = {
        "schema_version": "nim-agent-decision-v0.1",
        "decision_id": str(uuid4()),
        "recorded_at": datetime.now(UTC).isoformat(),
        "model": model,
        "command_sha256": sha256(command.encode("utf-8")).hexdigest() if command else None,
        "mission_spec_sha256": sha256(canonical_spec.encode("utf-8")).hexdigest() if canonical_spec else None,
        "mission_id": mission_spec.get("mission_id") if mission_spec else None,
        "plan_path": str(plan_path) if plan_path else None,
        "safety_decision": safety_decision,
        "outcome": outcome,
        "failure_reason": failure_reason,
    }
    path = directory / f"nim-agent-{record['decision_id']}.json"
    path.write_text(json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def main(arguments: Sequence[str] | None = None) -> None:
    asyncio.run(run(parse_arguments(arguments)))


if __name__ == "__main__":
    main()
