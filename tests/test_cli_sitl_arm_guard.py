"""Each fly_* mission CLI must refuse to arm when the SITL guard fails."""

from __future__ import annotations

import asyncio
import sys
import tempfile
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

from brain.cli import (
    fly_controlled_interruption,
    fly_return_to_home,
    fly_takeoff_hover_land,
    fly_waypoint_land,
    fly_waypoint_square_land,
)
from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.flight import InterruptionAction

from tests.test_cli_mission_artifacts import _mission, _profile

GUARD_CASES = (
    (fly_takeoff_hover_land, (), "authorize_takeoff_hover_land", "execute"),
    (fly_return_to_home, (), "authorize_takeoff_return_to_home", "execute_return_to_home_mission"),
    (fly_waypoint_land, (), "authorize_takeoff_waypoint_land", "execute_waypoint_mission"),
    (fly_waypoint_square_land, (), "authorize_takeoff_waypoint_square_land", "execute_waypoint_square_mission"),
    (
        fly_controlled_interruption,
        ("--interruption-action", "land"),
        "authorize_takeoff_interrupt_land",
        "execute_controlled_interruption_mission",
    ),
)


class CliSitlArmGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        free_link = patch("brain.cli.mavsdk_lifecycle.wait_for_free_link", return_value=True)
        free_link.start()
        self.addCleanup(free_link.stop)

    def test_each_flight_cli_refuses_before_arm_when_sitl_guard_fails(self) -> None:
        for module, arg_prefix, authorizer_name, executor_name in GUARD_CASES:
            with self.subTest(cli=module.__name__):
                adapter = MagicMock()
                adapter.connect = AsyncMock()
                setattr(adapter, executor_name, AsyncMock())
                arguments = module.parse_arguments((*arg_prefix, "--mavsdk-server-port", "51003"))
                mavsdk = ModuleType("mavsdk")
                mavsdk.System = MagicMock()  # type: ignore[attr-defined]
                sitl_guard = AsyncMock(side_effect=DemoPointRtlError("not SITL"))
                mission = _mission()
                if module is fly_controlled_interruption:
                    mission.interrupt_after_s = 2.0
                    mission.interruption_action = InterruptionAction.LAND
                    mission.hold_cleanup_s = 1.0
                with (
                    patch.dict(sys.modules, {"mavsdk": mavsdk}),
                    patch.object(module, "load_safety_profile", return_value=_profile()),
                    patch.object(module, authorizer_name, return_value=mission),
                    patch.object(module, "MavsdkMissionAdapter", return_value=adapter),
                    patch.object(module, "require_sitl_before_arm", sitl_guard),
                ):
                    with self.assertRaises(DemoPointRtlError):
                        asyncio.run(module.run(arguments))
                sitl_guard.assert_awaited_once()
                getattr(adapter, executor_name).assert_not_called()


if __name__ == "__main__":
    unittest.main()