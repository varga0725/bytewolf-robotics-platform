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
        default=None,
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
    parser.add_argument(
        "--approve-visual-review",
        nargs="+",
        type=Path,
        metavar="RUN.json",
        help="Mark visual_human_review approved on run evidence JSON files and refresh acceptance-latest.json.",
    )
    parser.add_argument(
        "--reviewer",
        default=None,
        help="Operator name recorded with --approve-visual-review.",
    )
    parser.add_argument(
        "--note",
        default=None,
        help="Optional note stored on approved visual review records.",
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


def _persist_run_document(document: dict[str, object]) -> None:
    directory = Path("simulation/artifacts/demo-waypoint-mission")
    payload = json.dumps(document, indent=2)
    (directory / "latest.json").write_text(payload, encoding="utf-8")
    run_name = document.get("evidence_run_filename")
    if isinstance(run_name, str) and (directory / run_name).is_file():
        (directory / run_name).write_text(payload, encoding="utf-8")


def _finalize_acceptance_document(
    document: dict[str, object],
    *,
    require_visual_evidence: bool,
) -> dict[str, object]:
    from brain.mission.demo_waypoint_mission import classify_acceptance_run

    classify_acceptance_run(document, require_visual_evidence=require_visual_evidence)
    _persist_run_document(document)
    return document


async def _run_once(
    *,
    endpoint: str,
    plan_file: Path,
    artifact_dir: Path,
    extra_args: list[str],
    require_visual_evidence: bool,
) -> dict[str, object]:
    from brain.cli import fly_demo_waypoint_mission

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
    return _finalize_acceptance_document(document, require_visual_evidence=require_visual_evidence)


def approve_visual_reviews(
    run_paths: list[Path],
    *,
    reviewer: str | None = None,
    note: str | None = None,
    require_visual_evidence: bool = True,
) -> dict[str, object]:
    from brain.mission.demo_waypoint_mission import (
        acceptance_run_technically_clean,
        classify_acceptance_run,
        consecutive_acceptance_summary,
    )

    documents: list[dict[str, object]] = []
    reviewed_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    for path in run_paths:
        if not path.is_file():
            raise RuntimeError(f"Run evidence file not found: {path}")
        document = json.loads(path.read_text(encoding="utf-8"))
        if not acceptance_run_technically_clean(document, require_visual_evidence=require_visual_evidence):
            raise RuntimeError(
                f"Refusing visual approval for non-clean run {path.name} "
                "(mission, ULog, trajectory, disarm, or capture failed)."
            )
        if document.get("visual_review_reason") == "frames_missing":
            raise RuntimeError(f"Refusing visual approval for {path.name}: review frames are missing.")
        if document.get("visual_review_required") is not True:
            raise RuntimeError(f"Refusing visual approval for {path.name}: visual_review_required is not set.")
        document["visual_human_review"] = "approved"
        document["visual_reviewed_at"] = reviewed_at
        if reviewer:
            document["visual_reviewer"] = reviewer
        if note:
            document["visual_review_note"] = note
        classify_acceptance_run(document, require_visual_evidence=require_visual_evidence)
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        _persist_run_document(document)
        documents.append(document)
    summary = consecutive_acceptance_summary(
        documents,
        required_runs=len(documents),
        require_visual_evidence=require_visual_evidence,
    )
    _write_summary(summary)
    return summary


def main(arguments: Sequence[str] | None = None) -> int:
    os.environ["BYTEWOLF_SITL_ACCEPTANCE"] = "1"
    parsed = parse_arguments(arguments)

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    if parsed.approve_visual_review:
        try:
            summary = approve_visual_reviews(
                list(parsed.approve_visual_review),
                reviewer=parsed.reviewer,
                note=parsed.note,
                require_visual_evidence=parsed.require_visual_evidence,
            )
        except Exception as error:
            print(str(error), file=sys.stderr)
            return 1
        print(f"Visual review approved for {len(parsed.approve_visual_review)} run(s).")
        print(f"Acceptance summary overall_status={summary.get('overall_status')}.")
        return 0 if summary.get("overall_status") == "passed" else 0

    if parsed.plan_file is None:
        print("--plan-file is required for acceptance runs.", file=sys.stderr)
        return 2
    if parsed.runs <= 0:
        print("--runs must be a positive integer.", file=sys.stderr)
        return 2

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
            if run_documents[-1].get("acceptance_status") == "failed":
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
    overall = summary.get("overall_status")
    if overall == "passed":
        print(f"Acceptance passed: {parsed.runs} consecutive clean runs.")
        return 0
    if overall == "pending_review":
        print(
            f"Acceptance pending visual review: {summary.get('per_run_status')}. "
            "Approve frames with --approve-visual-review <run.json> [...].",
        )
        return 0
    print(
        f"Acceptance failed: need {parsed.runs} consecutive passes, got {summary.get('per_run_status')}.",
        file=sys.stderr,
    )
    return 1


def _write_summary(summary: dict[str, object]) -> None:
    directory = Path("simulation/artifacts/demo-waypoint-mission")
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(summary, indent=2)
    (directory / f"acceptance-summary-{stamp}.json").write_text(payload, encoding="utf-8")
    (directory / "acceptance-latest.json").write_text(payload, encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())