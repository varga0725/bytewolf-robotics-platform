"""PX4 SITL launch scripts must bind MAVLink UDP to loopback via PX4_NET_INTERFACE."""

from __future__ import annotations

from pathlib import Path
import unittest


class SitlLoopbackMavlinkLaunchTests(unittest.TestCase):
    def test_loopback_helper_exports_px4_net_interface_lo(self) -> None:
        root = Path(__file__).resolve().parents[1]
        helper = (root / "simulation/gazebo/launch/apply_sitl_loopback_mavlink.zsh").read_text(encoding="utf-8")
        self.assertIn("PX4_NET_INTERFACE=lo", helper)

    def test_headless_launcher_sources_loopback_helper_before_px4(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "simulation/gazebo/launch/run_px4_gazebo_headless.zsh").read_text(encoding="utf-8")
        self.assertIn("apply_sitl_loopback_mavlink.zsh", launcher)
        loopback_pos = launcher.index("apply_sitl_loopback_mavlink.zsh")
        px4_start = launcher.index('"$PX4_BINARY" -d')
        self.assertLess(loopback_pos, px4_start)

    def test_interactive_launcher_sources_loopback_helper(self) -> None:
        root = Path(__file__).resolve().parents[1]
        launcher = (root / "simulation/gazebo/launch/run_px4_gazebo.zsh").read_text(encoding="utf-8")
        self.assertIn("apply_sitl_loopback_mavlink.zsh", launcher)


if __name__ == "__main__":
    unittest.main()