"""PX4 packed custom_mode decoding for acceptance harness."""

from __future__ import annotations

import unittest

from brain.mission.acceptance_inflight_px4_mode import (
    px4_auto_land_custom_mode,
    px4_auto_loiter_custom_mode,
    px4_auto_rtl_custom_mode,
    px4_custom_main_sub,
    px4_flight_mode_from_custom_mode,
)


class Px4CustomModeTests(unittest.TestCase):
    def test_auto_rtl_encoding(self) -> None:
        custom = px4_auto_rtl_custom_mode()
        self.assertEqual(px4_custom_main_sub(custom), (4, 5))
        self.assertEqual(px4_flight_mode_from_custom_mode(custom), "return_to_launch")

    def test_auto_loiter_encoding(self) -> None:
        custom = px4_auto_loiter_custom_mode()
        self.assertEqual(px4_custom_main_sub(custom), (4, 3))
        self.assertEqual(px4_flight_mode_from_custom_mode(custom), "loiter")

    def test_auto_land_encoding(self) -> None:
        custom = px4_auto_land_custom_mode()
        self.assertEqual(px4_custom_main_sub(custom), (4, 6))
        self.assertEqual(px4_flight_mode_from_custom_mode(custom), "land")

    def test_recorded_px4_sitl_rtl_value(self) -> None:
        """Value shape PX4 v1.17 SITL uses for AUTO RTL (main=4, sub=5)."""
        custom = (5 << 24) | (4 << 16)
        self.assertEqual(px4_flight_mode_from_custom_mode(custom), "return_to_launch")


if __name__ == "__main__":
    unittest.main()