"""Connection policy enforcement inside pymavlink acceptance helpers."""

from __future__ import annotations

import unittest

from brain.mission.acceptance_inflight_mavlink import GcsHeartbeatPublisher, send_emergency_kill
from brain.preflight_params.mavlink import MavlinkReadError


class AcceptanceInflightMavlinkPolicyTests(unittest.TestCase):
    def test_gcs_heartbeat_rejects_remote_host_before_connect(self) -> None:
        with self.assertRaises(MavlinkReadError):
            GcsHeartbeatPublisher("udpin://203.0.113.1:14540")

    def test_emergency_kill_rejects_bind_all_before_connect(self) -> None:
        with self.assertRaises(MavlinkReadError):
            send_emergency_kill("udpin://0.0.0.0:14540")


if __name__ == "__main__":
    unittest.main()