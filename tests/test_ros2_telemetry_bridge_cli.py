"""Argument coverage for the optional ROS 2 telemetry bridge entry point."""

import unittest

from brain.cli.ros2_telemetry_bridge import parse_arguments


class Ros2TelemetryBridgeCliTests(unittest.TestCase):
    def test_accepts_a_bounded_px4_discovery_timeout(self) -> None:
        arguments = parse_arguments(("--connection-timeout", "7.5"))

        self.assertEqual(arguments.connection_timeout, 7.5)


if __name__ == "__main__":
    unittest.main()
