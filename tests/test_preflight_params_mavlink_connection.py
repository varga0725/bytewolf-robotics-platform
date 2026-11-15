"""MAVSDK-style URI normalization for pymavlink (read-only)."""

from __future__ import annotations

import unittest

from brain.preflight_params.mavlink import MavlinkConnectionTarget, normalize_connection_url, resolve_mavlink_connection


class MavlinkConnectionTargetTests(unittest.TestCase):
    def test_qgc_udp_uri_unchanged(self) -> None:
        target = resolve_mavlink_connection("udpin://127.0.0.1:14540")
        self.assertEqual(target, MavlinkConnectionTarget("udpin:127.0.0.1:14540", {}))

    def test_serial_unix_path_preserves_device_and_splits_baud(self) -> None:
        target = resolve_mavlink_connection("serial:///dev/ttyACM0:57600")
        self.assertEqual(target.device, "/dev/ttyACM0")
        self.assertEqual(target.kwargs, {"baud": 57600})
        self.assertEqual(normalize_connection_url("serial:///dev/ttyACM0:57600"), "serial:/dev/ttyACM0@57600")

    def test_serial_cu_usbmodem_uri(self) -> None:
        target = resolve_mavlink_connection("serial:///dev/cu.usbmodem01:57600")
        self.assertEqual(target.device, "/dev/cu.usbmodem01")
        self.assertEqual(target.kwargs["baud"], 57600)

    def test_serial_windows_style_netloc(self) -> None:
        target = resolve_mavlink_connection("serial://COM3:57600")
        self.assertEqual(target.device, "COM3")
        self.assertEqual(target.kwargs["baud"], 57600)


if __name__ == "__main__":
    unittest.main()