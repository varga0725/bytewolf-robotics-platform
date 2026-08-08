"""Full-SITL local waypoint navigation through lidar, shield and Offboard."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from math import cos, radians
import os
from pathlib import Path
import subprocess
import time

from brain.adapters.offboard_fallback import OffboardFallbackExecutor
from brain.adapters.offboard_route_executor import OffboardRouteExecutor, RouteState
from brain.adapters.offboard_velocity_adapter import MavsdkVelocityAdapter
from brain.mission.commands import WaypointCommand
from brain.navigation.local_replanner import LocalReplanner
from brain.navigation.waypoints import GlobalPosition, relative_waypoint_to_global
from brain.safety.profile import SafetyProfile
from simulation.control.shield_run import _enabled, _latest_observation

_EARTH_RADIUS_M = 6_371_000.0
_SCAN_TOPIC = "/world/baylands/model/x500_mono_cam_down_0/link/link/sensor/lidar_2d_v2/scan"


class _StateSource:
    def __init__(self, drone, origin, command: WaypointCommand) -> None:
        self.drone, self.origin, self.command = drone, origin, command

    async def sample(self) -> RouteState:
        position, attitude = await asyncio.gather(
            anext(self.drone.telemetry.position()), anext(self.drone.telemetry.attitude_euler())
        )
        north = radians(position.latitude_deg-self.origin.latitude_deg)*_EARTH_RADIUS_M
        east = radians(position.longitude_deg-self.origin.longitude_deg)*_EARTH_RADIUS_M*cos(radians(self.origin.latitude_deg))
        return RouteState(
            self.command.north_m-north, self.command.east_m-east,
            self.command.target_altitude_m-float(position.relative_altitude_m),
            ((float(attitude.yaw_deg) + 180.0) % 360.0) - 180.0, datetime.now(UTC),
        )


class _ObstacleSource:
    def __init__(self, path: Path, vehicle_id: str) -> None:
        self.path, self.vehicle_id = path, vehicle_id

    def latest(self):
        return _latest_observation(self.path, vehicle_id=self.vehicle_id)


class SitlShieldedWaypointNavigator:
    """One callable used by the mission adapter for every local route leg."""

    def __init__(self, drone, profile: SafetyProfile, *, scan_path: Path = Path("var/runtime/lidar-scans.json")) -> None:
        self.drone, self.profile, self.scan_path = drone, profile, scan_path

    async def __call__(self, command: WaypointCommand) -> GlobalPosition:
        self.scan_path.parent.mkdir(parents=True, exist_ok=True)
        stream = self.scan_path.open("w", encoding="utf-8")
        environment = dict(os.environ, GZ_IP="127.0.0.1")
        capture = subprocess.Popen(
            ("gz", "topic", "-e", "-t", _SCAN_TOPIC, "--json-output"),
            stdout=stream, stderr=subprocess.DEVNULL, env=environment,
        )
        try:
            await asyncio.sleep(0.5)
            origin = await anext(self.drone.telemetry.position())
            target = relative_waypoint_to_global(
                GlobalPosition(origin.latitude_deg, origin.longitude_deg, origin.absolute_altitude_m),
                command, float(origin.relative_altitude_m),
            )
            active = replace(
                self.profile,
                # The generic mission envelope allows 5 m/s, but the current
                # conservative braking model would need 17 m of clear lidar at
                # that speed. The commissioned simulator route is deliberately
                # capped at 1 m/s, keeping its full-stop requirement below the
                # 9.9 m measured scene clearance.
                max_speed_m_s=min(1.0, self.profile.max_speed_m_s),
                offboard=_enabled(self.profile.offboard),
                shield=_enabled(self.profile.shield),
            )
            adapter = MavsdkVelocityAdapter(self.drone)
            executor = OffboardRouteExecutor(
                profile=active, adapter=adapter,
                fallback_executor=OffboardFallbackExecutor(self.drone, adapter),
                state_source=_StateSource(self.drone, origin, command),
                obstacle_source=_ObstacleSource(self.scan_path, self.profile.vehicle_id),
                now=lambda: datetime.now(UTC), monotonic=time.monotonic, sleep=asyncio.sleep,
                stream_hz=5.0, telemetry_max_age_s=0.5, max_vertical_speed_m_s=0.5,
                slowdown_radius_m=3.0,
                replanner=LocalReplanner(
                    lateral_speed_m_s=min(1.0, self.profile.max_speed_m_s*0.5),
                    max_detour_s=45.0,
                ),
            )
            await executor.run(arrival_tolerance_m=1.0, timeout_s=120.0)
            return target
        finally:
            capture.terminate()
            try:
                await asyncio.to_thread(capture.wait, 5)
            except subprocess.TimeoutExpired:
                capture.kill()
            stream.close()
