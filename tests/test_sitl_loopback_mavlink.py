"""Document PX4 v1.17 MAVLink UDP bind behaviour; do not assert ineffective env exports."""

from __future__ import annotations

from pathlib import Path
import unittest


class SitlMavlinkBindDocumentationTests(unittest.TestCase):
    def test_helper_stub_documents_v1_17_limitation(self) -> None:
        root = Path(__file__).resolve().parents[1]
        helper = (root / "simulation/gazebo/launch/apply_sitl_loopback_mavlink.zsh").read_text(encoding="utf-8")
        self.assertIn("v1.17", helper)
        self.assertNotIn("export PX4_NET_INTERFACE", helper)

    def test_handover_documents_inaddr_any_and_firewall_follow_up(self) -> None:
        root = Path(__file__).resolve().parents[1]
        handover = (root / "docs/handover-2026-07-27.md").read_text(encoding="utf-8")
        self.assertIn("v1.17", handover)
        self.assertIn("0.0.0.0", handover)
        self.assertIn("firewall", handover.lower())


if __name__ == "__main__":
    unittest.main()