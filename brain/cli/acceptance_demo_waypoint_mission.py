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


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run N consecutive clean demo-waypoint-mission flights against live SITL."
    )
    parser.add_argument("--runs", type=int, default=3, help="Required consecutive successful runs.")
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("BYTEWOLF_PX4_ENDPOINT", "udpin://127.0.0.1:14540"),
    )
    parser.add_argument(
        "--extra-cli-arg",
        action="append",
        default=[],
        help="Extra arguments forwarded to fly_demo_waypoint_mission (repeatable).",
    )
    return parser.parse_args(arguments)


async def _run_once(endpoint: str, extra_args: list[str]) -> dict[str, object]:
    from brain.cli import fly_demo_waypoint_mission

    arguments = fly_demo_waypoint_mission.parse_arguments(
        ["--endpoint", endpoint, *extra_args, "--deployment-mode", "simulation"]
    )
    await fly_demo_waypoint_mission.run(arguments)
    latest = Path("simulation/artifacts/demo-waypoint-mission/latest.json")
    if not latest.is_file():
        raise RuntimeError("Acceptance run did not write demo-waypoint-mission/latest.json.")
    return json.loads(latest.read_text(encoding="utf-8"))


def main(arguments: Sequence[str] | None = None) -> int:
    require_vars = {"BYTEWOLF_MISSION_EXECUTION_ENABLED": "true", "BYTEWOLF_SITL_ACCEPTANCE": "1"}
    for key, value in require_vars.items():
        os.environ[key] = value
    parsed = parse_arguments(arguments)
    if parsed.runs <= 0:
        print("--runs must be a positive integer.", file=sys.stderr)
        return 2

    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))

    run_documents: list[dict[str, object]] = []
    try:
        for index in range(parsed.runs):
            print(f"Starting demo-waypoint-mission acceptance run {index + 1}/{parsed.runs}...")
            run_documents.append(asyncio.run(_run_once(parsed.endpoint, list(parsed.extra_cli_arg or []))))
            if run_documents[-1].get("overall_status") != "passed":
                break
    except Exception as error:
        print(f"Run failed: {error}", file=sys.stderr)
        from brain.mission.demo_waypoint_mission import consecutive_acceptance_summary

        summary = consecutive_acceptance_summary(run_documents, required_runs=parsed.runs)
        summary["failure_reason"] = f"{type(error).__name__}: {error}"
        _write_summary(summary)
        return 1

    from brain.mission.demo_waypoint_mission import consecutive_acceptance_summary

    summary = consecutive_acceptance_summary(run_documents, required_runs=parsed.runs)
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
