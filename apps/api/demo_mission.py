"""Control Room launcher for the bounded demo-point-rtl SITL mission."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from brain.mission.demo_point_rtl import DemoPointRtlError, MISSION_TYPE, load_demo_point_rtl_config, require_simulation_target
from brain.safety.deployment import actuation_request_sha256, file_sha256
from brain.telemetry.link_lease import claim_link, default_lease_path, release_link, wait_for_free_link
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from shared.safety.execution_gate import require_mission_execution_enabled

_LOG_PATH = Path("var/runtime/demo-point-rtl.log")
_active_demo: subprocess.Popen[bytes] | None = None
_lock = threading.Lock()


class DemoMissionStartError(RuntimeError):
    """Raised when the demo cannot be submitted to the SITL executor."""


def start_demo_point_rtl() -> dict[str, object]:
    require_mission_execution_enabled()
    config = load_demo_point_rtl_config()
    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    deployment_mode = os.environ.get("BYTEWOLF_DEPLOYMENT_MODE", "simulation")
    endpoint = os.environ.get("BYTEWOLF_PX4_ENDPOINT", "udpin://127.0.0.1:14540")
    try:
        require_simulation_target(
            deployment_mode=deployment_mode,
            endpoint=endpoint,
            profile=profile,
            operation=MISSION_TYPE,
            request_sha256=actuation_request_sha256(
                {
                    "operation": MISSION_TYPE,
                    "vehicle_id": profile.vehicle_id,
                    "north_m": config.north_m,
                    "east_m": config.east_m,
                    "takeoff_altitude_m": config.takeoff_altitude_m,
                }
            ),
            safety_profile_sha256=file_sha256(DEFAULT_SAFETY_PROFILE_PATH),
        )
    except DemoPointRtlError as error:
        raise DemoMissionStartError(str(error)) from error
    global _active_demo
    with _lock:
        if _active_demo is not None and _active_demo.poll() is None:
            raise DemoMissionStartError("A SITL demo mission is already running.")
        lease_path = default_lease_path()
        lease_path.parent.mkdir(parents=True, exist_ok=True)
        claim_link("mission", pid=os.getpid(), path=lease_path)
        if not wait_for_free_link():
            release_link(lease_path)
            raise DemoMissionStartError(
                "The PX4 link is still held by another process, so no demo was started."
            )
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        log = _LOG_PATH.open("w", encoding="utf-8")
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "brain.cli.fly_demo_point_rtl",
                "--deployment-mode",
                "simulation",
                "--endpoint",
                endpoint,
            ],
            start_new_session=True,
            stdout=log,
            stderr=subprocess.STDOUT,
            env={
                **os.environ,
                "BYTEWOLF_MISSION_EXECUTION_ENABLED": "true",
                "BYTEWOLF_DEPLOYMENT_MODE": deployment_mode,
            },
        )
        _active_demo = process
        claim_link("mission", pid=process.pid, path=lease_path)
        threading.Thread(
            target=_release_link_when_finished,
            args=(process, log, lease_path),
            daemon=True,
        ).start()
    return {
        "status": "submitted",
        "mission_type": MISSION_TYPE,
        "configured_point_m": {
            "north_m": config.north_m,
            "east_m": config.east_m,
            "altitude_m": config.waypoint_altitude_m,
        },
        "log_path": str(_LOG_PATH),
    }


def _release_link_when_finished(process: subprocess.Popen[bytes], log, lease_path: Path) -> None:
    global _active_demo
    try:
        process.wait()
    finally:
        release_link(lease_path)
        with _lock:
            if _active_demo is process:
                _active_demo = None
        try:
            log.close()
        except OSError:
            pass
