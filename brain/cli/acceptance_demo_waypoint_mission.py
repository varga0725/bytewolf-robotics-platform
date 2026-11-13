"""Live demo-waypoint-mission acceptance with consecutive-run gate."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from collections.abc import Sequence
from uuid import uuid4

from shared.safety.execution_gate import require_mission_execution_enabled


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run N consecutive clean demo-waypoint-mission flights against live SITL."
    )
    parser.add_argument("--runs", type=int, default=3, help="Required consecutive successful runs.")
    parser.add_argument(
        "--plan-file",
        type=Path,
        required=True,
        help="Reviewed demo-waypoint-mission plan with a matching approval record.",
    )
    parser.add_argument(
        "--record-operator-approval",
        action="store_true",
        help="Write the sha256-bound approval record after explicit --confirm-plan-execution.",
    )
    parser.add_argument(
        "--confirm-plan-execution",
        action="store_true",
        help="Required with --record-operator-approval to attest operator intent.",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("BYTEWOLF_PX4_ENDPOINT", "udpin://127.0.0.1:14540"),
    )
    parser.add_argument(
        "--artifact-root",
        type=Path,
        default=Path("simulation/artifacts/demo-waypoint-mission/runs"),
        help="Per-run artifact directories (ULog manifests) are created here.",
    )
    parser.add_argument(
        "--extra-cli-arg",
        action="append",
        default=[],
        help="Extra arguments forwarded to fly_demo_waypoint_mission (repeatable).",
    )
    parser.add_argument(
        "--require-visual-evidence",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Require screen capture status 'captured' or a flight screenshot artifact.",
    )
    return parser.parse_args(arguments)


def _ensure_plan_approval(plan_file: Path, *, record_approval: bool, confirm: bool) -> None:
    from brain.mission.demo_waypoint_reviewed_plan import (
        require_matching_demo_waypoint_approval,
        write_operator_approval_for_plan,
    )

    if not plan_file.is_file():
        raise RuntimeError(f"Plan file '{plan_file}' does not exist.")
    approval_path = plan_file.with_name(f"{plan_file.name}.approval.json")
    if approval_path.is_file():
        require_matching_demo_waypoint_approval(plan_file)
        return
    if not record_approval:
        raise RuntimeError(
            "Plan has no approval record. Complete Control Room review/approve or pass "
            "--record-operator-approval with --confirm-plan-execution."
        )
    if not confirm:
        raise RuntimeError("Operator approval requires --confirm-plan-execution.")
    require_mission_execution_enabled()
    write_operator_approval_for_plan(plan_file)
    require_matching_demo_waypoint_approval(plan_file)


async def _run_once(
    *,
    endpoint: str,
    plan_file: Path,
    artifact_dir: Path,
    extra_args: list[str],
    require_visual_evidence: bool,
) -> dict[str, object]:
    from brain.cli import fly_demo_waypoint_mission
    from brain.mission.demo_waypoint_mission import acceptance_run_is_clean

    arguments = fly_demo_waypoint_mission.parse_arguments(
        [
            "--endpoint",
            endpoint,
            "--plan-file",
            str(plan_file),
            "--artifact-dir",
            str(artifact_dir),
            *extra_args,
            "--deployment-mode",
            "simulation",
        ]
    )
    await fly_demo_waypoint_mission.run(arguments)
    latest = Path("simulation/artifacts/demo-waypoint-mission/latest.json")
    if not latest.is_file():
        raise RuntimeError("Acceptance run did not write demo-waypoint-mission/latest.json.")
    document = json.loads(latest.read_text(encoding="utf-8"))
    if not acceptance_run_is_clean(document, require_visual_evidence=require_visual_evidence):
        document["overall_status"] = "failed"
        if not document.get("px4_ulog_relative_path"):
            document["ulog_status"] = "missing"
        if document.get("disarm_reason"):
            document.setdefault(
                "failure_reason",
                f"disarm used fallback path: {document.get('disarm_reason')}",
            )
        elif require_visual_evidence:
            document.setdefault(
                "failure_reason",
                "visual evidence missing (need screen_capture.status=='captured' or screenshot)",
            )
        latest.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def main(arguments: Sequence[str] | None = None) -> int:
    os.environ["BYTEWOLF_SITL_ACCEPTANCE"] = "1"
    parsed = parse_arguments(arguments)
    if parsed.runs <= 0:
        print("--runs must be a positive integer.", file=sys.stderr)
        return 2

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    try:
        require_mission_execution_enabled()
        _ensure_plan_approval(
            parsed.plan_file,
            record_approval=parsed.record_operator_approval,
            confirm=parsed.confirm_plan_execution,
        )
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 2

    parsed.artifact_root.mkdir(parents=True, exist_ok=True)
    run_documents: list[dict[str, object]] = []
    try:
        for index in range(parsed.runs):
            print(f"Starting demo-waypoint-mission acceptance run {index + 1}/{parsed.runs}...")
            run_dir = parsed.artifact_root / f"run-{index + 1}-{uuid4()}"
            run_dir.mkdir(parents=True, exist_ok=True)
            run_documents.append(
                asyncio.run(
                    _run_once(
                        endpoint=parsed.endpoint,
                        plan_file=parsed.plan_file,
                        artifact_dir=run_dir,
                        extra_args=list(parsed.extra_cli_arg or []),
                        require_visual_evidence=parsed.require_visual_evidence,
                    )
                )
            )
            if run_documents[-1].get("overall_status") != "passed":
                break
    except Exception as error:
        print(f"Run failed: {error}", file=sys.stderr)
        from brain.mission.demo_waypoint_mission import consecutive_acceptance_summary

        summary = consecutive_acceptance_summary(
            run_documents,
            required_runs=parsed.runs,
            require_visual_evidence=parsed.require_visual_evidence,
        )
        summary["failure_reason"] = f"{type(error).__name__}: {error}"
        _write_summary(summary)
        return 1

    from brain.mission.demo_waypoint_mission import consecutive_acceptance_summary

    summary = consecutive_acceptance_summary(
        run_documents,
        required_runs=parsed.runs,
        require_visual_evidence=parsed.require_visual_evidence,
    )
    _write_summary(summary)
    if summary["overall_status"] != "passed":
        print(
            f"Acceptance failed: need {parsed.runs} consecutive passes, got {summary['per_run_status']}.",
            file=sys.stderr,
        )
        return 1
    print(f"Acceptance passed: {parsed.runs} consecutive clean runs.")
    return 0


def _write_summary(summary: dict[str, object]) -> None:
    directory = Path("simulation/artifacts/demo-waypoint-mission")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(summary, indent=2)
    (directory / f"acceptance-summary-{stamp}.json").write_text(payload, encoding="utf-8")
    (directory / "acceptance-latest.json").write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
