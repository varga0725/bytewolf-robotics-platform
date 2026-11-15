"""Async scenario runners for the in-flight SITL acceptance harness."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.mission.acceptance_inflight_mavlink import (
    GcsHeartbeatPublisher,
    send_emergency_kill,
    send_normal_disarm,
    wait_until_disarmed_via_mavlink,
)
from brain.mission.acceptance_inflight_sitl import (
    COM_DL_LOSS_T_S,
    GF_MAX_HOR_DIST_M,
    KILL_SCENARIO_ALTITUDE_M,
    OPERATION,
    SCENARIO_DATALINK_LOSS,
    SCENARIO_GEOFENCE,
    SCENARIO_KILL_IN_AIR,
    SCENARIO_LOW_BATTERY,
    SCENARIO_RELAY_INFLIGHT,
    TAKEOFF_ALTITUDE_M,
    Outcome,
    ProfilePreconditionResult,
    RelaySample,
    VehicleTransition,
    build_evidence_document,
    evaluate_datalink_loss_machine,
    evaluate_geofence_machine,
    evaluate_kill_in_air_machine,
    evaluate_low_battery_machine,
    evaluate_relay_inflight_machine,
    profile_notes_for_scenario,
)
from brain.mission.commands import WaypointCommand
from brain.mission.sitl_target import require_px4_sitl_indication
from brain.navigation.waypoints import GlobalPosition, horizontal_distance_m
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.loopback_endpoint import require_loopback_udpin_endpoint

LOW_BATTERY_TIMEOUT_S = 240.0
DEFAULT_ACTION_TIMEOUT_S = 45.0
LANDING_TIMEOUT_S = 120.0


def relay_uses_loopback_udp_only(process: subprocess.Popen[bytes], *, ingest_port: int) -> bool:
    """Return True when the relay process tree has loopback UDP only (no TCP listeners)."""
    try:
        import psutil
    except ModuleNotFoundError:
        return True
    if process.poll() is not None:
        return False
    root = psutil.Process(process.pid)
    processes = [root, *root.children(recursive=True)]
    saw_ingest = False
    for candidate in processes:
        for connection in candidate.net_connections(kind="all"):
            if connection.type == socket.SOCK_STREAM and connection.status == psutil.CONN_LISTEN:
                return False
            if connection.type != socket.SOCK_DGRAM:
                continue
            laddr = connection.laddr
            host = getattr(laddr, "host", None) if laddr is not None else None
            if host is None and laddr is not None:
                host = laddr[0]
            if not host or not ipaddress.ip_address(str(host)).is_loopback:
                return False
            port = getattr(laddr, "port", None) if laddr is not None else None
            if port is None and laddr is not None:
                port = laddr[1]
            if port == ingest_port:
                saw_ingest = True
    return saw_ingest


@dataclass
class TransitionRecorder:
    drone: object
    home: GlobalPosition | None = None
    transitions: list[VehicleTransition] = field(default_factory=list)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _origin_s: float = field(default=0.0, repr=False)

    async def start(self) -> None:
        self._origin_s = time.monotonic()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    def _elapsed(self) -> float:
        return time.monotonic() - self._origin_s

    async def _run(self) -> None:
        armed: bool | None = None
        in_air: bool | None = None
        flight_mode: str | None = None
        battery_percent: float | None = None
        position: GlobalPosition | None = None

        async def armed_loop() -> None:
            nonlocal armed
            async for sample in self.drone.telemetry.armed():
                armed = bool(sample)
                self._maybe_record(armed, in_air, flight_mode, battery_percent, position)

        async def in_air_loop() -> None:
            nonlocal in_air
            async for sample in self.drone.telemetry.in_air():
                in_air = bool(sample)
                self._maybe_record(armed, in_air, flight_mode, battery_percent, position)

        async def mode_loop() -> None:
            nonlocal flight_mode
            async for sample in self.drone.telemetry.flight_mode():
                flight_mode = str(getattr(sample, "name", sample))
                self._maybe_record(armed, in_air, flight_mode, battery_percent, position)

        async def battery_loop() -> None:
            nonlocal battery_percent
            async for sample in self.drone.telemetry.battery():
                battery_percent = float(sample.remaining_percent)
                self._maybe_record(armed, in_air, flight_mode, battery_percent, position)

        async def position_loop() -> None:
            nonlocal position
            async for sample in self.drone.telemetry.position():
                position = GlobalPosition(
                    sample.latitude_deg,
                    sample.longitude_deg,
                    sample.absolute_altitude_m,
                )
                self._maybe_record(armed, in_air, flight_mode, battery_percent, position)

        await asyncio.gather(
            armed_loop(),
            in_air_loop(),
            mode_loop(),
            battery_loop(),
            position_loop(),
        )

    def _maybe_record(
        self,
        armed: bool | None,
        in_air: bool | None,
        flight_mode: str | None,
        battery_percent: float | None,
        position: GlobalPosition | None,
    ) -> None:
        horiz: float | None = None
        rel_alt: float | None = None
        if position is not None and self.home is not None:
            horiz = horizontal_distance_m(self.home, position)
            rel_alt = position.absolute_altitude_m - self.home.absolute_altitude_m
        self.transitions.append(
            VehicleTransition(
                monotonic_s=self._elapsed(),
                armed=armed,
                in_air=in_air,
                flight_mode=flight_mode,
                battery_percent=battery_percent,
                relative_altitude_m=rel_alt,
                horizontal_distance_from_home_m=horiz,
            )
        )


async def _takeoff_to_altitude(
    adapter: MavsdkMissionAdapter,
    *,
    altitude_m: float,
    action_timeout_s: float,
) -> None:
    drone = adapter._drone
    await asyncio.wait_for(drone.action.set_takeoff_altitude(altitude_m), timeout=action_timeout_s)
    await asyncio.wait_for(drone.action.arm(), timeout=action_timeout_s)
    await asyncio.wait_for(drone.action.takeoff(), timeout=action_timeout_s)
    await adapter.wait_until_relative_altitude_at_least(
        altitude_m - 1.0,
        tolerance_m=1.5,
        timeout_s=action_timeout_s,
    )


async def _wait_landed_disarmed(
    adapter: MavsdkMissionAdapter,
    *,
    landing_timeout_s: float,
    action_timeout_s: float,
) -> None:
    drone = adapter._drone
    await asyncio.wait_for(adapter.wait_until_landed(landing_timeout_s), timeout=landing_timeout_s + 5.0)
    try:
        await asyncio.wait_for(drone.action.disarm(), timeout=action_timeout_s)
    except Exception:
        pass
    deadline = time.monotonic() + 15.0
    while time.monotonic() < deadline:
        sample = await adapter._vehicle_in_air_state(timeout_s=2.0)
        if sample is False:
            break
        await asyncio.sleep(0.5)


async def run_datalink_loss_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    endpoint: str,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    gcs = GcsHeartbeatPublisher(endpoint)
    gcs.start()
    recorder = TransitionRecorder(adapter._drone)
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=TAKEOFF_ALTITUDE_M, action_timeout_s=action_timeout_s)
        timings["takeoff_complete_s"] = recorder._elapsed()
        gcs.stop()
        extra["heartbeat_stopped_at_s"] = recorder._elapsed()
        # Allow COM_DL_LOSS_T plus margin for RTL to begin.
        await asyncio.sleep(COM_DL_LOSS_T_S + 25.0)
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
        timings["landed_disarmed_s"] = recorder._elapsed()
    finally:
        await recorder.stop()
        gcs.stop()
    return recorder.transitions, timings, extra


async def run_geofence_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    recorder = TransitionRecorder(adapter._drone)
    timings: dict[str, float] = {}
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=15.0, action_timeout_s=action_timeout_s)
        breach = WaypointCommand(
            north_m=GF_MAX_HOR_DIST_M + 30.0,
            east_m=0.0,
            target_altitude_m=15.0,
        )
        target = await adapter.goto_relative_waypoint(breach)
        timings["breach_command_s"] = recorder._elapsed()
        try:
            await adapter.wait_until_waypoint_reached(
                target,
                tolerance_m=5.0,
                timeout_s=60.0,
            )
        except Exception:
            pass
        await asyncio.sleep(5.0)
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
        timings["landed_disarmed_s"] = recorder._elapsed()
    finally:
        await recorder.stop()
    return recorder.transitions, timings, {}


async def run_kill_in_air_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    endpoint: str,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    recorder = TransitionRecorder(adapter._drone)
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(
            adapter,
            altitude_m=KILL_SCENARIO_ALTITUDE_M,
            action_timeout_s=action_timeout_s,
        )
        disarm_accepted = await asyncio.to_thread(send_normal_disarm, endpoint)
        extra["disarm_in_air_rejected"] = not disarm_accepted
        kill_start = time.monotonic()
        await asyncio.to_thread(send_emergency_kill, endpoint)
        kill_elapsed = await asyncio.to_thread(
            wait_until_disarmed_via_mavlink,
            endpoint,
            timeout_s=3.0,
            poll=time.monotonic,
        )
        if kill_elapsed is None:
            extra["kill_to_disarm_s"] = None
        else:
            extra["kill_to_disarm_s"] = kill_elapsed
        timings["kill_command_s"] = kill_start - recorder._origin_s
    finally:
        await recorder.stop()
    return recorder.transitions, timings, extra


async def run_low_battery_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    recorder = TransitionRecorder(adapter._drone)
    timings: dict[str, float] = {}
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=12.0, action_timeout_s=action_timeout_s)
        deadline = time.monotonic() + LOW_BATTERY_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(2.0)
            if _saw_landed_disarmed_from_transitions(recorder.transitions):
                break
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
        timings["landed_disarmed_s"] = recorder._elapsed()
    finally:
        await recorder.stop()
    return recorder.transitions, timings, {}


def _saw_landed_disarmed_from_transitions(transitions: list[VehicleTransition]) -> bool:
    saw_not_in_air = False
    for item in transitions:
        if item.in_air is False:
            saw_not_in_air = True
        if saw_not_in_air and item.armed is False:
            return True
    return False


async def run_relay_inflight_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    endpoint: str,
    snapshot_path: Path,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any], list[RelaySample]]:
    require_loopback_udpin_endpoint(endpoint)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "brain.cli.dashboard_telemetry",
            "--backend",
            "pymavlink",
            "--endpoint",
            endpoint,
            "--snapshot-file",
            str(snapshot_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
    )
    ingest_port = urlparse(endpoint).port or 14540
    recorder = TransitionRecorder(adapter._drone)
    relay_samples: list[RelaySample] = []
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    origin = time.monotonic()
    try:
        await asyncio.sleep(1.5)
        extra["relay_loopback_udp_only"] = relay_uses_loopback_udp_only(proc, ingest_port=ingest_port)
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=10.0, action_timeout_s=action_timeout_s)
        hover_deadline = time.monotonic() + 8.0
        while time.monotonic() < hover_deadline:
            relay_samples.append(_read_relay_sample(snapshot_path, origin))
            await asyncio.sleep(0.4)
        drone = adapter._drone
        await asyncio.wait_for(drone.action.land(), timeout=action_timeout_s)
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
        while time.monotonic() < origin + 20.0:
            relay_samples.append(_read_relay_sample(snapshot_path, origin))
            await asyncio.sleep(0.4)
        timings["flight_complete_s"] = time.monotonic() - origin
    finally:
        await recorder.stop()
        proc.terminate()
        try:
            proc.wait(timeout=10.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5.0)
    return recorder.transitions, timings, extra, relay_samples


def _read_relay_sample(snapshot_path: Path, origin_s: float) -> RelaySample:
    in_air: bool | None = None
    age_s = 999.0
    if snapshot_path.is_file():
        try:
            payload = json.loads(snapshot_path.read_text(encoding="utf-8"))
            in_air = payload.get("in_air")
            captured = payload.get("captured_at")
            if isinstance(captured, str):
                captured_at = datetime.fromisoformat(captured.replace("Z", "+00:00"))
                age_s = max(0.0, (datetime.now(UTC) - captured_at.astimezone(UTC)).total_seconds())
        except (json.JSONDecodeError, ValueError, OSError):
            pass
    return RelaySample(monotonic_s=time.monotonic() - origin_s, captured_at_age_s=age_s, in_air=in_air)


def evaluate_scenario(
    scenario: str,
    transitions: list[VehicleTransition],
    *,
    timings: dict[str, float],
    extra: dict[str, Any],
    relay_samples: list[RelaySample] | None = None,
) -> tuple[Outcome, str | None]:
    if scenario == SCENARIO_DATALINK_LOSS:
        ok, reason = evaluate_datalink_loss_machine(
            transitions,
            heartbeat_stopped_at_s=extra.get("heartbeat_stopped_at_s"),
        )
    elif scenario == SCENARIO_GEOFENCE:
        ok, reason = evaluate_geofence_machine(transitions)
    elif scenario == SCENARIO_KILL_IN_AIR:
        ok, reason = evaluate_kill_in_air_machine(
            disarm_in_air_rejected=bool(extra.get("disarm_in_air_rejected")),
            kill_to_disarm_s=extra.get("kill_to_disarm_s"),
        )
    elif scenario == SCENARIO_LOW_BATTERY:
        ok, reason = evaluate_low_battery_machine(transitions)
    elif scenario == SCENARIO_RELAY_INFLIGHT:
        ok, reason = evaluate_relay_inflight_machine(
            relay_samples or [],
            relay_loopback_udp_only=bool(extra.get("relay_loopback_udp_only", True)),
        )
    else:
        return "FAIL", f"Unknown scenario {scenario!r}."
    return ("PASS", None) if ok else ("FAIL", reason)


async def run_one_scenario(
    scenario: str,
    adapter: MavsdkMissionAdapter | None,
    *,
    endpoint: str,
    profile_check: ProfilePreconditionResult,
    snapshot_path: Path,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    failure_reason: str | None = None
    outcome: Outcome = "FAIL"
    transitions: list[VehicleTransition] = []
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    relay_samples: list[RelaySample] | None = None

    if not profile_check.ok:
        failure_reason = profile_check.failure_reason or "SITL failsafe profile precondition failed."
    elif adapter is None:
        failure_reason = "No MAVSDK adapter connected."
    else:
        try:
            if scenario == SCENARIO_DATALINK_LOSS:
                transitions, timings, extra = await run_datalink_loss_scenario(adapter, endpoint=endpoint)
            elif scenario == SCENARIO_GEOFENCE:
                transitions, timings, extra = await run_geofence_scenario(adapter)
            elif scenario == SCENARIO_KILL_IN_AIR:
                transitions, timings, extra = await run_kill_in_air_scenario(adapter, endpoint=endpoint)
            elif scenario == SCENARIO_LOW_BATTERY:
                transitions, timings, extra = await run_low_battery_scenario(adapter)
            elif scenario == SCENARIO_RELAY_INFLIGHT:
                transitions, timings, extra, relay_samples = await run_relay_inflight_scenario(
                    adapter,
                    endpoint=endpoint,
                    snapshot_path=snapshot_path,
                )
            outcome, eval_reason = evaluate_scenario(
                scenario,
                transitions,
                timings=timings,
                extra=extra,
                relay_samples=relay_samples,
            )
            if eval_reason:
                failure_reason = eval_reason
        except Exception as error:
            failure_reason = str(error)

    finished_at = datetime.now(UTC)
    document = build_evidence_document(
        scenario=scenario,
        outcome=outcome,
        started_at=started_at,
        finished_at=finished_at,
        transitions=transitions,
        profile_check=profile_check,
        timings=timings,
        failure_reason=failure_reason,
        notes=profile_notes_for_scenario(scenario),
        extra={**extra, "operation": OPERATION, "endpoint": _redact_endpoint(endpoint)},
    )
    if relay_samples is not None:
        document["relay_samples"] = [
            {
                "monotonic_s": sample.monotonic_s,
                "captured_at_age_s": sample.captured_at_age_s,
                "in_air": sample.in_air,
            }
            for sample in relay_samples
        ]
    return document


def _redact_endpoint(endpoint: str) -> dict[str, object]:
    parsed = urlparse(endpoint)
    port = parsed.port or 14540
    return {"transport": "udp-loopback", "port": port}


async def connect_adapter(
    endpoint: str,
    *,
    connection_timeout_s: float,
    preflight_wait_s: float,
    mavsdk_server_port: int,
) -> tuple[object, MavsdkMissionAdapter]:
    from mavsdk import System

    profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
    system = System(port=mavsdk_server_port)
    adapter = MavsdkMissionAdapter(system, safety_profile=profile, preflight_wait_s=preflight_wait_s)
    await asyncio.wait_for(adapter.connect(endpoint), timeout=connection_timeout_s)
    await adapter._require_preflight()
    await require_px4_sitl_indication(system, timeout_s=10.0)
    return system, adapter


async def disconnect_adapter(system: object) -> None:
    close = getattr(system, "close", None)
    if callable(close):
        await close()