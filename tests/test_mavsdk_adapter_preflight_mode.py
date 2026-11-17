"""Preflight must not send hold on read-only paths; missions still leave Land/RTL."""

from __future__ import annotations

import asyncio
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from brain.adapters.mavsdk_adapter import MavsdkMissionAdapter
from brain.cli import check_boot_prearm
from brain.mission.commands import TakeoffCommand
from brain.mission.flight import TakeoffHoverLandMission
from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH, load_safety_profile
from brain.telemetry.link_lease import LEASE_PATH_ENV

from tests.test_mavsdk_adapter import Battery, FakeAction, FakeDrone, Health, Position


class _ModeTelemetry:
    def __init__(self, mode: str, *, in_air_sequence: tuple[bool, ...] = (False,)) -> None:
        self._mode = mode
        self._in_air = in_air_sequence

    async def flight_mode(self):
        while True:
            yield SimpleNamespace(name=self._mode)
            await asyncio.sleep(0)

    async def in_air(self):
        for sample in self._in_air:
            yield sample
            await asyncio.sleep(0)
        while True:
            yield False
            await asyncio.sleep(0)

    async def health(self):
        yield Health(global_position_ok=True, home_position_ok=True)

    async def home(self):
        yield Position()

    async def position(self):
        while True:
            yield Position()
            await asyncio.sleep(0)

    async def battery(self):
        while True:
            yield Battery(remaining_percent=75.0)
            await asyncio.sleep(0)


class _HoldRecordingAction(FakeAction):
    async def hold(self) -> None:
        self._events.append("hold")


class _LandModeDrone(FakeDrone):
    def __init__(self, mode: str, *, in_air_sequence: tuple[bool, ...] = (False,)) -> None:
        super().__init__()
        self.telemetry = _ModeTelemetry(mode, in_air_sequence=in_air_sequence)
        self.action = _HoldRecordingAction(self.events)


class _SwitchingLandTelemetry(_ModeTelemetry):
    """Simulate PX4 leaving LAND after hold() by flipping the reported mode."""

    def __init__(self) -> None:
        super().__init__("LAND", in_air_sequence=(False, True, False))
        self._after_hold = False

    async def flight_mode(self):
        while True:
            mode = "HOLD" if self._after_hold else "LAND"
            yield SimpleNamespace(name=mode)
            await asyncio.sleep(0)

    def mark_hold_sent(self) -> None:
        self._after_hold = True


class _SwitchingHoldAction(_HoldRecordingAction):
    def __init__(self, events: list[object], telemetry: _SwitchingLandTelemetry) -> None:
        super().__init__(events)
        self._telemetry = telemetry

    async def hold(self) -> None:
        await super().hold()
        self._telemetry.mark_hold_sent()


class _SwitchingLandDrone(FakeDrone):
    def __init__(self) -> None:
        super().__init__()
        telemetry = _SwitchingLandTelemetry()
        self.telemetry = telemetry
        self.action = _SwitchingHoldAction(self.events, telemetry)


class PreflightModeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._sitl_patcher = patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            new_callable=AsyncMock,
            return_value={"status": "verified", "matched_signals": []},
        )
        self._sitl_patcher.start()
        self.addCleanup(self._sitl_patcher.stop)

        lease_dir = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(lease_dir, ignore_errors=True))
        self._previous_lease = os.environ.get(LEASE_PATH_ENV)
        os.environ[LEASE_PATH_ENV] = str(Path(lease_dir) / "mavlink-link.lease")
        self.addCleanup(self._restore_lease)

        free_link = patch("brain.cli.mavsdk_lifecycle.wait_for_free_link", return_value=True)
        free_link.start()
        self.addCleanup(free_link.stop)

    def _restore_lease(self) -> None:
        if self._previous_lease is None:
            os.environ.pop(LEASE_PATH_ENV, None)
        else:
            os.environ[LEASE_PATH_ENV] = self._previous_lease

    async def test_verify_preflight_never_calls_hold_in_land_or_rtl_on_ground(self) -> None:
        for mode in ("LAND", "RETURN_TO_LAUNCH"):
            with self.subTest(mode=mode):
                drone = _LandModeDrone(mode)
                adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.0)
                await adapter.verify_preflight()
                self.assertEqual(drone.events, [], mode)

    async def test_verify_preflight_never_calls_hold_in_hold_mode(self) -> None:
        drone = _LandModeDrone("HOLD")
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.0)
        await adapter.verify_preflight()
        self.assertEqual(drone.events, [])

    async def test_require_preflight_with_mode_change_calls_hold_in_land(self) -> None:
        drone = _LandModeDrone("LAND")
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.0)
        await adapter._require_preflight(allow_mode_change=True)
        self.assertEqual(drone.events, ["hold"])

    async def test_require_preflight_without_mode_change_skips_hold_in_land(self) -> None:
        drone = _LandModeDrone("LAND")
        adapter = MavsdkMissionAdapter(drone, preflight_wait_s=0.0)
        await adapter._require_preflight(allow_mode_change=False)
        self.assertEqual(drone.events, [])

    async def test_execute_still_leaves_land_before_arming(self) -> None:
        drone = _SwitchingLandDrone()
        adapter = MavsdkMissionAdapter(drone, sleep=asyncio.sleep, preflight_wait_s=0.0)
        mission = TakeoffHoverLandMission(TakeoffCommand(2.0), hover_duration_s=0.0)
        await adapter.execute(mission)
        self.assertEqual(drone.events.count("hold"), 1)
        self.assertIn("arm", drone.events)
        self.assertLess(drone.events.index("hold"), drone.events.index("arm"))

    async def test_check_boot_prearm_runs_read_only_verify_and_prints_truthfully(self) -> None:
        drone = _LandModeDrone("LAND")
        drone._stop_mavsdk_server = MagicMock()  # type: ignore[attr-defined]
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=drone)  # type: ignore[attr-defined]

        stdout = io.StringIO()
        arguments = check_boot_prearm.parse_arguments(
            ("--mavsdk-server-port", "51099", "--preflight-wait-seconds", "1")
        )
        profile = load_safety_profile(DEFAULT_SAFETY_PROFILE_PATH)
        with (
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
            patch.object(check_boot_prearm, "load_safety_profile", return_value=profile),
            patch("builtins.print", lambda *args, **kwargs: stdout.write(" ".join(map(str, args)) + "\n")),
        ):
            await check_boot_prearm.run(arguments)

        self.assertEqual(drone.events, [])
        self.assertIn("No flight command was sent", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()