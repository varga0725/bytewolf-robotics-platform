"""Async scenario runners for the in-flight SITL acceptance harness."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from brain.mission.acceptance_inflight_mavlink import (
    DisarmAttemptResult,
    GcsHeartbeatPublisher,
    MavlinkSample,
    MavlinkTelemetryPoller,
    relay_uses_loopback_udp_only,
    send_emergency_kill,
    send_normal_disarm,
    wait_until_disarmed_via_mavlink,
)
from brain.mission.acceptance_inflight_ports import mavlink_endpoint_for_gcs
from brain.mission.acceptance_inflight_sitl import (
    COM_DL_LOSS_T_S,
    DATALINK_DLL_HOLD_S,
    DATALINK_LANDED_MAX_AFTER_STOP_S,
    GF_MAX_HOR_DIST_M,
    KILL_SCENARIO_ALTITUDE_M,
    OPERATION,
    RELAY_MAX_CAPTURE_AGE_S_DEFAULT,
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
    check_sitl_failsafe_profile_precondition,
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

LOW_BATTERY_TIMEOUT_S = 300.0
DEFAULT_ACTION_TIMEOUT_S = 45.0
LANDING_TIMEOUT_S = 120.0
GEOFENCE_INSIDE_GOTO_M = 92.0
GEOFENCE_PUSH_SPEED_M_S = 4.0
GEOFENCE_PUSH_DURATION_S = 18.0


@dataclass
class TransitionRecorder:
    drone: object
    home: GlobalPosition | None = None
    transitions: list[VehicleTransition] = field(default_factory=list)
    _task: asyncio.Task[None] | None = field(default=None, repr=False)
    _origin_s: float = field(default=0.0, repr=False)
    mavlink_poller: MavlinkTelemetryPoller | None = None

    async def start(self) -> None:
        self._origin_s = time.monotonic()
        self._task = asyncio.create_task(self._run())
        if self.mavlink_poller is not None:
            self.mavlink_poller.origin_s = self._origin_s
            self.mavlink_poller.start()

    async def stop(self) -> None:
        if self.mavlink_poller is not None:
            self.mavlink_poller.stop()
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

        await asyncio.gather(armed_loop(), in_air_loop(), mode_loop(), battery_loop(), position_loop())

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

    def merge_mavlink_samples(self, samples: list[MavlinkSample]) -> None:
        for item in samples:
            self.transitions.append(
                VehicleTransition(
                    monotonic_s=item.monotonic_s,
                    armed=item.armed,
                    in_air=item.in_air,
                    flight_mode=item.flight_mode,
                    battery_percent=item.battery_percent,
                )
            )
        self.transitions.sort(key=lambda row: row.monotonic_s)


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


async def _vehicle_is_disarmed(adapter: MavsdkMissionAdapter) -> bool:
    drone = adapter._drone
    try:
        armed = await asyncio.wait_for(anext(drone.telemetry.armed()), timeout=3.0)
        return armed is False
    except (StopAsyncIteration, TimeoutError, asyncio.TimeoutError):
        return False


async def _vehicle_is_landed(adapter: MavsdkMissionAdapter) -> bool:
    in_air = await adapter._vehicle_in_air_state(timeout_s=2.0)
    return in_air is False


async def _wait_landed_disarmed(
    adapter: MavsdkMissionAdapter,
    *,
    landing_timeout_s: float,
    action_timeout_s: float,
) -> None:
    if await _vehicle_is_disarmed(adapter) and await _vehicle_is_landed(adapter):
        return
    drone = adapter._drone
    if not await _vehicle_is_landed(adapter):
        await asyncio.wait_for(adapter.wait_until_landed(landing_timeout_s), timeout=landing_timeout_s + 5.0)
    if not await _vehicle_is_disarmed(adapter):
        try:
            await asyncio.wait_for(drone.action.disarm(), timeout=action_timeout_s)
        except Exception:
            pass
    deadline = time.monotonic() + 20.0
    while time.monotonic() < deadline:
        if await _vehicle_is_disarmed(adapter) and await _vehicle_is_landed(adapter):
            return
        await asyncio.sleep(0.5)
    raise RuntimeError("Vehicle did not confirm landed and disarmed.")


async def _safe_recover_vehicle(
    adapter: MavsdkMissionAdapter | None,
    *,
    mavlink_endpoint: str,
    landing_timeout_s: float = LANDING_TIMEOUT_S,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> None:
    if adapter is None:
        try:
            await asyncio.to_thread(
                wait_until_disarmed_via_mavlink,
                mavlink_endpoint,
                timeout_s=5.0,
            )
        except Exception:
            pass
        return
    try:
        if await _vehicle_is_disarmed(adapter) and await _vehicle_is_landed(adapter):
            return
        drone = adapter._drone
        try:
            if not await _vehicle_is_landed(adapter):
                await asyncio.wait_for(drone.action.land(), timeout=action_timeout_s)
        except Exception:
            pass
        await _wait_landed_disarmed(
            adapter,
            landing_timeout_s=landing_timeout_s,
            action_timeout_s=action_timeout_s,
        )
    except Exception:
        try:
            await asyncio.to_thread(send_emergency_kill, mavlink_endpoint)
            await asyncio.to_thread(wait_until_disarmed_via_mavlink, mavlink_endpoint, timeout_s=5.0)
        except Exception:
            pass


async def _push_north_past_geofence(adapter: MavsdkMissionAdapter, *, duration_s: float, speed_m_s: float) -> None:
    from mavsdk.offboard import VelocityBodyYawspeed

    drone = adapter._drone
    await drone.offboard.set_velocity_body(VelocityBodyYawspeed(speed_m_s, 0, 0, 0))
    await drone.offboard.start()
    try:
        await asyncio.sleep(duration_s)
    finally:
        await drone.offboard.stop()


async def run_datalink_loss_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    system: object,
    mavsdk_endpoint: str,
    mavlink_endpoint: str,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    gcs = GcsHeartbeatPublisher(mavlink_endpoint)
    gcs.start()
    recorder = TransitionRecorder(adapter._drone)
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    poller: MavlinkTelemetryPoller | None = None
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=TAKEOFF_ALTITUDE_M, action_timeout_s=action_timeout_s)
        takeoff_complete_s = recorder._elapsed()
        gcs.stop()
        await recorder.stop()
        loss_origin = time.monotonic()
        extra["heartbeat_stopped_at_s"] = 0.0
        stop_owned_mavsdk_server(system)
        poller = MavlinkTelemetryPoller(mavlink_endpoint)
        poller.origin_s = loss_origin
        poller.start()
        deadline = DATALINK_LANDED_MAX_AFTER_STOP_S
        while time.monotonic() - loss_origin < deadline:
            await asyncio.sleep(1.0)
            if _mavlink_samples_landed_disarmed(poller.samples, after_s=0.0):
                break
        timings["landed_disarmed_s"] = time.monotonic() - loss_origin
        timings["takeoff_complete_s"] = takeoff_complete_s
        transitions = _mavlink_to_transitions(poller.samples)
    finally:
        if poller is not None:
            poller.stop()
        gcs.stop()
        await recorder.stop()
    return transitions, timings, extra


def _mavlink_to_transitions(samples: list[MavlinkSample]) -> list[VehicleTransition]:
    return [
        VehicleTransition(
            monotonic_s=sample.monotonic_s,
            armed=sample.armed,
            in_air=sample.in_air,
            flight_mode=sample.flight_mode,
            battery_percent=sample.battery_percent,
        )
        for sample in samples
    ]


def _mavlink_samples_landed_disarmed(samples: list[MavlinkSample], *, after_s: float) -> bool:
    saw_not_in_air = False
    for sample in samples:
        if sample.monotonic_s < after_s:
            continue
        if sample.in_air is False:
            saw_not_in_air = True
        if saw_not_in_air and sample.armed is False:
            return True
    return False


async def run_geofence_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    mavlink_endpoint: str,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any]]:
    poller = MavlinkTelemetryPoller(mavlink_endpoint)
    recorder = TransitionRecorder(adapter._drone, mavlink_poller=poller)
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    try:
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=15.0, action_timeout_s=action_timeout_s)
        inside = WaypointCommand(north_m=GEOFENCE_INSIDE_GOTO_M, east_m=0.0, target_altitude_m=15.0)
        target = await adapter.goto_relative_waypoint(inside)
        timings["inside_waypoint_s"] = recorder._elapsed()
        try:
            await adapter.wait_until_waypoint_reached(target, tolerance_m=8.0, timeout_s=90.0)
        except Exception:
            pass
        await _push_north_past_geofence(
            adapter,
            duration_s=GEOFENCE_PUSH_DURATION_S,
            speed_m_s=GEOFENCE_PUSH_SPEED_M_S,
        )
        timings["velocity_push_end_s"] = recorder._elapsed()
        extra["geofence_breach_at_s"] = poller.geofence_breach_at_s
        await asyncio.sleep(5.0)
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
        timings["landed_disarmed_s"] = recorder._elapsed()
    finally:
        await recorder.stop()
    recorder.merge_mavlink_samples(poller.samples)
    return recorder.transitions, timings, extra


async def run_kill_in_air_scenario(
    adapter: MavsdkMissionAdapter,
    *,
    mavlink_endpoint: str,
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
        disarm: DisarmAttemptResult = await asyncio.to_thread(send_normal_disarm, mavlink_endpoint)
        extra["disarm_outcome"] = disarm.outcome
        kill_start = time.monotonic()
        await asyncio.to_thread(send_emergency_kill, mavlink_endpoint)
        kill_elapsed = await asyncio.to_thread(
            wait_until_disarmed_via_mavlink,
            mavlink_endpoint,
            timeout_s=3.0,
        )
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
        if not _saw_landed_disarmed_from_transitions(recorder.transitions):
            await _wait_landed_disarmed(
                adapter,
                landing_timeout_s=LANDING_TIMEOUT_S,
                action_timeout_s=action_timeout_s,
            )
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
    mavlink_endpoint: str,
    snapshot_path: Path,
    relay_max_age_s: float,
    action_timeout_s: float = DEFAULT_ACTION_TIMEOUT_S,
) -> tuple[list[VehicleTransition], dict[str, float], dict[str, Any], list[RelaySample]]:
    require_loopback_udpin_endpoint(mavlink_endpoint)
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "brain.cli.dashboard_telemetry",
            "--backend",
            "pymavlink",
            "--endpoint",
            mavlink_endpoint,
            "--snapshot-file",
            str(snapshot_path),
        ],
        cwd=Path(__file__).resolve().parents[2],
    )
    ingest_port = urlparse(mavlink_endpoint).port or 14550
    recorder = TransitionRecorder(adapter._drone)
    relay_samples: list[RelaySample] = []
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {"relay_max_age_s": relay_max_age_s}
    origin = time.monotonic()
    try:
        await asyncio.sleep(1.5)
        loopback_ok = relay_uses_loopback_udp_only(proc, ingest_port=ingest_port)
        extra["relay_loopback_udp_only"] = loopback_ok
        extra["relay_loopback_check_performed"] = True
        relay_samples.append(_read_relay_sample(snapshot_path, origin))
        home = await adapter._global_position_sample("home")
        recorder.home = home
        await recorder.start()
        await _takeoff_to_altitude(adapter, altitude_m=10.0, action_timeout_s=action_timeout_s)
        hover_deadline = time.monotonic() + 10.0
        while time.monotonic() < hover_deadline:
            relay_samples.append(_read_relay_sample(snapshot_path, origin))
            await asyncio.sleep(0.4)
        drone = adapter._drone
        await asyncio.wait_for(drone.action.land(), timeout=action_timeout_s)
        while time.monotonic() < origin + 25.0:
            relay_samples.append(_read_relay_sample(snapshot_path, origin))
            await asyncio.sleep(0.4)
            if _saw_landed_disarmed_from_transitions(recorder.transitions):
                break
        await _wait_landed_disarmed(adapter, landing_timeout_s=LANDING_TIMEOUT_S, action_timeout_s=action_timeout_s)
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
        ok, reason = evaluate_geofence_machine(
            transitions,
            geofence_breach_at_s=extra.get("geofence_breach_at_s"),
        )
    elif scenario == SCENARIO_KILL_IN_AIR:
        ok, reason = evaluate_kill_in_air_machine(
            disarm_outcome=extra.get("disarm_outcome"),
            kill_to_disarm_s=extra.get("kill_to_disarm_s"),
        )
    elif scenario == SCENARIO_LOW_BATTERY:
        ok, reason = evaluate_low_battery_machine(transitions)
    elif scenario == SCENARIO_RELAY_INFLIGHT:
        ok, reason = evaluate_relay_inflight_machine(
            relay_samples or [],
            max_age_s=float(extra.get("relay_max_age_s", RELAY_MAX_CAPTURE_AGE_S_DEFAULT)),
            relay_loopback_udp_only=bool(extra.get("relay_loopback_udp_only")),
            relay_loopback_check_performed=bool(extra.get("relay_loopback_check_performed")),
        )
    else:
        return "FAIL", f"Unknown scenario {scenario!r}."
    return ("PASS", None) if ok else ("FAIL", reason)


async def run_one_scenario(
    scenario: str,
    adapter: MavsdkMissionAdapter | None,
    *,
    system: object | None,
    mavsdk_endpoint: str,
    mavlink_endpoint: str,
    profile_check: ProfilePreconditionResult,
    snapshot_path: Path,
    relay_max_age_s: float,
    px4_run_directory: Path | None,
    px4_build_directory: Path,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    failure_reason: str | None = None
    outcome: Outcome = "FAIL"
    transitions: list[VehicleTransition] = []
    timings: dict[str, float] = {}
    extra: dict[str, Any] = {}
    relay_samples: list[RelaySample] | None = None

    profile_check = check_sitl_failsafe_profile_precondition(
        px4_run_directory=px4_run_directory,
        px4_build_directory=px4_build_directory,
    )

    try:
        if not profile_check.ok:
            failure_reason = profile_check.failure_reason or "SITL failsafe profile precondition failed."
        elif adapter is None or system is None:
            failure_reason = "No MAVSDK adapter connected."
        else:
            if scenario == SCENARIO_DATALINK_LOSS:
                transitions, timings, extra = await run_datalink_loss_scenario(
                    adapter,
                    system=system,
                    mavsdk_endpoint=mavsdk_endpoint,
                    mavlink_endpoint=mavlink_endpoint,
                )
            elif scenario == SCENARIO_GEOFENCE:
                transitions, timings, extra = await run_geofence_scenario(
                    adapter,
                    mavlink_endpoint=mavlink_endpoint,
                )
            elif scenario == SCENARIO_KILL_IN_AIR:
                transitions, timings, extra = await run_kill_in_air_scenario(
                    adapter,
                    mavlink_endpoint=mavlink_endpoint,
                )
            elif scenario == SCENARIO_LOW_BATTERY:
                transitions, timings, extra = await run_low_battery_scenario(adapter)
            elif scenario == SCENARIO_RELAY_INFLIGHT:
                transitions, timings, extra, relay_samples = await run_relay_inflight_scenario(
                    adapter,
                    mavlink_endpoint=mavlink_endpoint,
                    snapshot_path=snapshot_path,
                    relay_max_age_s=relay_max_age_s,
                )
            else:
                failure_reason = f"Unknown scenario {scenario!r}."
            if failure_reason is None:
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
        failure_reason = str(error) or error.__class__.__name__
        outcome = "FAIL"
    finally:
        try:
            needs_recovery = scenario != SCENARIO_KILL_IN_AIR or extra.get("kill_to_disarm_s") is None
            if needs_recovery:
                await _safe_recover_vehicle(adapter, mavlink_endpoint=mavlink_endpoint)
        except Exception as cleanup_error:
            if failure_reason is None:
                failure_reason = f"Cleanup failed: {cleanup_error}"

    finished_at = datetime.now(UTC)
    if outcome != "PASS" and not failure_reason:
        failure_reason = "Scenario failed without an explicit reason."
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
        extra={
            **extra,
            "operation": OPERATION,
            "mavsdk_endpoint": _redact_endpoint(mavsdk_endpoint),
            "mavlink_endpoint": _redact_endpoint(mavlink_endpoint),
        },
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
    port = parsed.port or (14540 if "14540" in endpoint else 14550)
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