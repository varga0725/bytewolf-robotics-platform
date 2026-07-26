"""Fly the interrupted-stream scenario against a real PX4. Isolated from scoring.

Kept apart from ``offboard_watchdog_scenario`` so the scoring stays importable
and unit-testable on a machine with no MAVSDK, no Gazebo and no PX4. Everything
here needs all three.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
import subprocess
import time
import uuid

from brain.adapters.offboard_fallback import OffboardFallbackExecutor
from brain.adapters.offboard_velocity_adapter import MavsdkVelocityAdapter
from brain.control.contract import load_setpoint
from brain.control.session import OffboardSession
from brain.safety.profile import load_safety_profile
from simulation.control.offboard_watchdog_scenario import (
    ModeSample,
    OffboardWatchdogReport,
    evaluate_offboard_watchdog,
)


_LAUNCHER = (
    Path(__file__).resolve().parents[2] / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh"
)
_ENDPOINT = "udpin://0.0.0.0:14540"

#: The stream rate. Comfortably inside the twin's max_rate_hz and well above
#: PX4's 2 Hz minimum for holding Offboard mode.
_STREAM_HZ = 10.0

#: A gentle forward creep. The point of the scenario is the interruption, not
#: the manoeuvre, and a slow command keeps the vehicle near where it started.
_FORWARD_M_S = 0.3


def run_offboard_watchdog_scenario(
    artifact_directory: Path,
    *,
    altitude_m: float = 3.0,
    stream_seconds: float = 4.0,
    observe_seconds: float = 20.0,
    startup_wait_s: float = 32.0,
) -> OffboardWatchdogReport:
    """Start SITL, fly the interruption, and write the evidence artifact."""
    artifact_directory.mkdir(parents=True, exist_ok=True)
    environment = {**os.environ, "GZ_IP": "127.0.0.1"}
    launcher = subprocess.Popen(
        (str(_LAUNCHER), "base"),
        cwd=_LAUNCHER.resolve().parents[3],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
    )
    try:
        time.sleep(startup_wait_s)
        report = asyncio.run(
            _fly(
                altitude_m=altitude_m,
                stream_seconds=stream_seconds,
                observe_seconds=observe_seconds,
            )
        )
    finally:
        launcher.terminate()
        try:
            launcher.wait(timeout=30.0)
        except subprocess.TimeoutExpired:
            launcher.kill()

    destination = artifact_directory / f"offboard-watchdog-{_stamp()}.json"
    destination.write_text(
        __import__("json").dumps(report.as_document(), indent=2) + "\n", encoding="utf-8"
    )
    print(f"evidence: {destination}")
    return report


async def _fly(*, altitude_m: float, stream_seconds: float, observe_seconds: float):
    from mavsdk import System

    profile = load_safety_profile()
    assert profile.offboard is not None, "The twin declares no offboard block."

    drone = System()
    await drone.connect(system_address=_ENDPOINT)
    await _await_connection(drone)

    modes: list[ModeSample] = []
    started_at = time.monotonic()
    mode_watch = asyncio.create_task(_watch_modes(drone, modes, started_at))
    try:
        await drone.action.arm()
        await drone.action.set_takeoff_altitude(altitude_m)
        await drone.action.takeoff()
        await asyncio.sleep(8.0)

        # The twin ships with Offboard disabled, which is correct as a default
        # and wrong for a scenario whose whole purpose is to exercise it. The
        # override lives here, in the scenario, so the shipped default stays
        # false for every other caller.
        limits = _enabled(profile.offboard)
        scenario_profile = _profile_with(profile, limits)

        adapter = MavsdkVelocityAdapter(drone)
        session = OffboardSession(scenario_profile, adapter, shadow=False)

        silence_began_at_s = await _stream_then_go_silent(
            session, adapter, seconds=stream_seconds, started_at=started_at
        )

        # No setpoint is offered from here on: a producer that simply stopped,
        # the way a crashed one does. What is NOT silent is the link -- MAVSDK
        # re-sends the last setpoint at 20 Hz on its own, so PX4 still sees a
        # healthy stream. That is precisely why the fallback has to be executed
        # rather than merely decided.
        watchdog_stopped_at_s: float | None = None
        fallback_executed_at_s: float | None = None
        fallback_record = None
        executor = OffboardFallbackExecutor(drone, adapter)
        deadline = time.monotonic() + observe_seconds
        while time.monotonic() < deadline:
            decision = session.poll(datetime.now(UTC))
            if decision.requires_fallback and watchdog_stopped_at_s is None:
                watchdog_stopped_at_s = round(time.monotonic() - started_at, 3)
                fallback_executed_at_s = watchdog_stopped_at_s
                fallback_record = await executor.execute(decision.fallback)
            await asyncio.sleep(0.1)

        # Cleanup after the experiment, not part of it. The scoring discounts
        # anything from here on so a tidy-up cannot be read as the vehicle
        # reacting.
        cleanup_at_s = round(time.monotonic() - started_at, 3)
        await adapter.stop()
        await _land(drone)
    finally:
        mode_watch.cancel()
        try:
            await mode_watch
        except asyncio.CancelledError:
            pass

    return evaluate_offboard_watchdog(
        setpoints_offered=session.record.offered,
        setpoints_approved=session.record.approved,
        setpoints_delivered=session.record.delivered,
        watchdog_stopped_at_s=watchdog_stopped_at_s,
        fallback=session.record.fallbacks,
        rejections=session.record.rejections,
        mode_samples=modes,
        silence_began_at_s=silence_began_at_s,
        intervened_at_s=cleanup_at_s,
        fallback_executed_at_s=fallback_executed_at_s,
        fallback_steps=None if fallback_record is None else fallback_record.as_document(),
        recorded_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
    )


async def _stream_then_go_silent(session, adapter, *, seconds: float, started_at: float) -> float:
    """Stream setpoints for a while, then stop sending and return when."""
    stream_id = f"scenario-{uuid.uuid4().hex[:8]}"
    interval_s = 1.0 / _STREAM_HZ
    sequence = 0
    entered_offboard = False
    finish_at = time.monotonic() + seconds
    while time.monotonic() < finish_at:
        now = datetime.now(UTC)
        setpoint = load_setpoint(
            {
                "contract_version": "v0.1",
                "vehicle_id": session_vehicle_id(session),
                "stream_id": stream_id,
                "sequence": sequence,
                "issued_at": now.isoformat().replace("+00:00", "Z"),
                "ttl_s": 0.3,
                "frame": "body_frd",
                "kind": "velocity",
                "validity": "valid",
                "velocity": {
                    "x_m_s": _FORWARD_M_S, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0
                },
            }
        )
        await session.deliver(session.offer(setpoint, now), now)
        sequence += 1
        if not entered_offboard:
            # PX4 refuses Offboard without a stream already running, so the
            # mode is entered after the first setpoint, never before.
            await adapter.start()
            entered_offboard = True
        await asyncio.sleep(interval_s)
    return round(time.monotonic() - started_at, 3)


def session_vehicle_id(session) -> str:
    """The twin id the session validates against."""
    return session._boundary._profile.vehicle_id  # noqa: SLF001 - scenario introspection


async def _watch_modes(drone, modes: list[ModeSample], started_at: float) -> None:
    """Record PX4's own account of what mode it is in."""
    async for mode in drone.telemetry.flight_mode():
        name = str(mode).rsplit(".", 1)[-1]
        if not modes or modes[-1].mode != name:
            modes.append(ModeSample(at_s=round(time.monotonic() - started_at, 3), mode=name))


async def _await_connection(drone) -> None:
    async for state in drone.core.connection_state():
        if state.is_connected:
            return


async def _land(drone) -> None:
    try:
        await drone.action.land()
        await asyncio.sleep(10.0)
    except Exception:  # noqa: BLE001 - the evidence is already recorded
        return


def _enabled(limits):
    from dataclasses import replace as dataclass_replace

    return dataclass_replace(limits, enabled=True)


def _profile_with(profile, limits):
    from dataclasses import replace as dataclass_replace

    return dataclass_replace(profile, offboard=limits)


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
