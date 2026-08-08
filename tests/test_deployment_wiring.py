"""Every MAVSDK entry point must declare deployment semantics."""

from __future__ import annotations

import inspect
import unittest

from apps.gateway import telegram_mission_gateway
from brain.cli import (
    check_boot_prearm,
    check_usb_bench,
    dashboard_telemetry,
    fly_controlled_interruption,
    fly_nim_mission,
    fly_return_to_home,
    fly_takeoff_hover_land,
    fly_waypoint_land,
    fly_waypoint_square_land,
    ros2_telemetry_bridge,
)


ACTUATION_CLIS = (
    (fly_takeoff_hover_land, ()),
    (fly_waypoint_land, ()),
    (fly_return_to_home, ()),
    (fly_waypoint_square_land, ()),
    (fly_controlled_interruption, ("--interruption-action", "hold")),
    (fly_nim_mission, ("--mission-spec-file", "reviewed.json")),
)


class DeploymentArgumentWiringTests(unittest.TestCase):
    def test_every_flight_cli_defaults_to_simulation_and_has_no_permit(self) -> None:
        for module, required in ACTUATION_CLIS:
            with self.subTest(cli=module.__name__):
                arguments = module.parse_arguments(required)
                self.assertEqual(arguments.deployment_mode, "simulation")
                self.assertIsNone(arguments.physical_actuation_permit)
                self.assertEqual(arguments.endpoint, "udpin://127.0.0.1:14540")

    def test_every_flight_cli_authorizes_before_importing_mavsdk(self) -> None:
        for module, _required in ACTUATION_CLIS:
            with self.subTest(cli=module.__name__):
                source = inspect.getsource(module.run)
                self.assertLess(
                    source.index("authorize_actuation_connection("),
                    source.index("from mavsdk import System"),
                )

    def test_read_only_entry_points_also_declare_a_mode(self) -> None:
        cases = (
            (check_usb_bench, (), "bench_readonly"),
            (check_boot_prearm, (), "simulation"),
            (dashboard_telemetry, (), "simulation"),
            (ros2_telemetry_bridge, (), "simulation"),
        )
        for module, required, expected in cases:
            with self.subTest(cli=module.__name__):
                arguments = module.parse_arguments(required)
                self.assertEqual(arguments.deployment_mode, expected)

    def test_gateway_fixes_subprocess_execution_to_simulation(self) -> None:
        source = inspect.getsource(telegram_mission_gateway._execute_with_cli)
        self.assertIn('"--deployment-mode",\n                "simulation",', source)
        self.assertNotIn("arguments.deployment_mode", source)


if __name__ == "__main__":
    unittest.main()
