"""Flight-long in_air observation for demo disarm."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile


class _LandedState:
    def __init__(self, name: str) -> None:
        self.name = name


class DemoFlightObservationDisarmTests(unittest.IsolatedAsyncioTestCase):
    def _adapter(self) -> MavsdkMissionAdapter:
        adapter = MavsdkMissionAdapter(MagicMock(), safety_profile=load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
        adapter._drone = MagicMock()
        adapter._drone.action.disarm = AsyncMock()
        return adapter

    @staticmethod
    def _async_stream(*values):
        async def _gen():
            for value in values:
                yield value
                await asyncio.sleep(0)

        return MagicMock(side_effect=lambda: _gen())

    async def test_flight_observer_records_in_air_before_landing_wait(self) -> None:
        adapter = self._adapter()
        await adapter._start_flight_observation_tracking()
        adapter._drone.telemetry.in_air = self._async_stream(True, False)
        adapter._drone.telemetry.landed_state = self._async_stream(
            _LandedState("IN_AIR"),
            _LandedState("ON_GROUND"),
        )
        await asyncio.sleep(0.05)
        await adapter._stop_flight_observation_tracking()
        self.assertTrue(adapter._mission_observed_airborne())

    async def test_disarms_without_fallback_when_airborne_observed_during_flight(self) -> None:
        adapter = self._adapter()
        adapter._observed_in_air = True
        adapter.wait_until_landed = AsyncMock()
        adapter._drone.telemetry.in_air = self._async_stream(False)
        adapter._drone.telemetry.landed_state = self._async_stream(_LandedState("ON_GROUND"))

        await adapter._demo_disarm_when_landed(2.0, 2.0)

        adapter._drone.action.disarm.assert_awaited_once()
        self.assertIsNone(getattr(adapter, "_demo_disarm_reason", None))


if __name__ == "__main__":
    unittest.main()
