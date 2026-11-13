"""Submit reviewed plans to the SITL CLIs after the execution gate passes."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

from apps.gateway.telegram_mission_gateway import (
    _DEFAULT_PLAN_DIRECTORY,
    _MISSION_LOG_PATH,
    _PLAN_NAME,
    _active_execution,
    _execution_lock,
    _release_link_when_finished,
)
from brain.mission.demo_reviewed_plan import require_matching_demo_approval
from brain.telemetry.link_lease import claim_link, default_lease_path, release_link, wait_for_free_link
from shared.safety.execution_gate import MissionExecutionQuarantinedError, require_mission_execution_enabled


def execute_reviewed_plan(plan_name: str) -> str:
    require_mission_execution_enabled()

    plan_path = _DEFAULT_PLAN_DIRECTORY / plan_name
    if not _PLAN_NAME.fullmatch(plan_name) or not plan_path.is_file():
        raise RuntimeError("Reviewed plan does not exist.")
    _validate_plan_before_spawn(plan_path)

    global _active_execution
    with _execution_lock:
        if _active_execution is not None and _active_execution.poll() is None:
            raise RuntimeError("A SITL mission is already running.")
        lease_path = default_lease_path()
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        claim_link("mission", pid=os.getpid(), path=lease_path)
        if not wait_for_free_link():
            release_link(lease_path)
            raise RuntimeError(
                "The PX4 link is still held by another process, so no mission was started."
            )
        command = _execution_command(plan_path)
        log_path = _MISSION_LOG_PATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log = log_path.open("w")
        _active_execution = subprocess.Popen(
            command,
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=dict(os.environ),
        )
        claim_link("mission", pid=_active_execution.pid, path=lease_path)
        threading.Thread(
            target=_release_link_when_finished,
            args=(_active_execution, log, lease_path),
            daemon=True,
        ).start()
    return "submitted after local safety validation"


def _validate_plan_before_spawn(plan_path: Path) -> None:
    if plan_path.name.endswith(".demo-point-rtl.json"):
        require_matching_demo_approval(plan_path)
        return
    from brain.cli.fly_nim_mission import _load_approved_plan
    from brain.mission_spec.validation import load_mission_safety_profile
    from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH

    _load_approved_plan(plan_path, load_mission_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))


def _execution_command(plan_path: Path) -> list[str]:
    endpoint = os.environ.get("BYTEWOLF_PX4_ENDPOINT", "udpin://127.0.0.1:14540")
    if plan_path.name.endswith(".demo-point-rtl.json"):
        return [
            sys.executable,
            "-m",
            "brain.cli.fly_demo_point_rtl",
            "--deployment-mode",
            "simulation",
            "--plan-file",
            str(plan_path),
            "--endpoint",
            endpoint,
        ]

    return [
        sys.executable,
        "-m",
        "brain.cli.fly_nim_mission",
        "--mission-spec-file",
        str(plan_path),
        "--execute",
        "--deployment-mode",
        "simulation",
        "--endpoint",
        endpoint,
    ]
