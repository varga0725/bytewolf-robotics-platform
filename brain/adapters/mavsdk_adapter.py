"""MAVSDK adapter for executing an approved, bounded mission on PX4."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from brain.mission.execution import MissionExecution, MissionPhase
from brain.mission.flight import TakeoffHoverLandMission


class MavsdkAction(Protocol):
    async def set_takeoff_altitude(self, altitude_m: float) -> None: ...

    async def arm(self) -> None: ...

    async def takeoff(self) -> None: ...

    async def land(self) -> None: ...


class MavsdkCore(Protocol):
    def connection_state(self): ...


class MavsdkDrone(Protocol):
    action: MavsdkAction
    core: MavsdkCore

    async def connect(self, system_address: str) -> None: ...


class MavsdkMissionAdapter:
    """Executes an already approved mission, never raw motor commands."""

    def __init__(
        self,
        drone: MavsdkDrone,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self._drone = drone
        self._sleep = sleep

    async def connect(self, system_address: str) -> None:
        """Connect to the configured MAVLink endpoint and await discovery."""
        await self._drone.connect(system_address=system_address)
        async for state in self._drone.core.connection_state():
            if state.is_connected:
                return

    async def execute(self, mission: TakeoffHoverLandMission) -> MissionExecution:
        """Execute a mission and return its immutable audit trail."""
        execution = MissionExecution.empty().transition(MissionPhase.ARMING)
        await self._drone.action.set_takeoff_altitude(mission.takeoff.target_altitude_m)
        try:
            await self._drone.action.arm()
            execution = execution.transition(MissionPhase.TAKING_OFF)
            await self._drone.action.takeoff()
            execution = execution.transition(MissionPhase.HOVERING)
            await self._sleep(mission.hover_duration_s)
        except BaseException:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            finally:
                execution = execution.transition(MissionPhase.FAILED)
            raise
        else:
            execution = execution.transition(MissionPhase.LANDING)
            try:
                await self._drone.action.land()
            except BaseException:
                execution = execution.transition(MissionPhase.FAILED)
                raise
            return execution.transition(MissionPhase.COMPLETED)
            await self._drone.action.land()
