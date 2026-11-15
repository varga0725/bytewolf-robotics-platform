"""Unit tests for the read-only pymavlink dashboard telemetry relay."""

from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from brain.telemetry.mavsdk_relay import DashboardTelemetryState
from brain.telemetry.pymavlink_relay import apply_mavlink_message, run_pymavlink_relay


class _Message:
    def __init__(self, message_type: str, **fields: float | int) -> None:
        self._type = message_type
        for key, value in fields.items():
            setattr(self, key, value)

    def get_type(self) -> str:
        return self._type


class PymavlinkMappingTests(unittest.TestCase):
    def test_messages_build_the_dashboard_snapshot_contract(self) -> None:
        state = DashboardTelemetryState()
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = apply_mavlink_message(
            state,
            _Message(
                "GLOBAL_POSITION_INT",
                lat=474000000,
                lon=190500000,
                alt=120500,
                relative_alt=2500,
            ),
            observed_at=now,
        )
        state = apply_mavlink_message(
            state,
            _Message("SYS_STATUS", battery_remaining=88),
            observed_at=now,
        )
        state = apply_mavlink_message(
            state,
            _Message("EXTENDED_SYS_STATE", landed_state=2),
            observed_at=now,
        )
        state = apply_mavlink_message(
            state,
            _Message("ATTITUDE", yaw=1.57),
            observed_at=now,
        )

        document = state.as_dashboard_document(now)

        self.assertAlmostEqual(document["position"]["latitude_deg"], 47.4)
        self.assertAlmostEqual(document["battery"]["remaining_percent"], 88.0)
        self.assertTrue(document["in_air"])
        self.assertIn("heading_deg", document)
        self.assertEqual(document["captured_at"], "2026-07-22T09:00:00Z")


class PymavlinkRelayLoopTests(unittest.TestCase):
    def test_stale_timeout_rewrites_without_advancing_captured_at(self) -> None:
        clock = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        times = [
            0.0,
            0.0,
            11.0,
        ]
        index = 0

        def monotonic() -> float:
            nonlocal index
            if index < len(times):
                value = times[index]
                index += 1
                return value
            return times[-1]

        messages = [
            _Message(
                "GLOBAL_POSITION_INT",
                lat=474000000,
                lon=190500000,
                alt=120500,
                relative_alt=2500,
            ),
            _Message("SYS_STATUS", battery_remaining=90),
            _Message("EXTENDED_SYS_STATE", landed_state=1),
            None,
        ]
        message_index = 0

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    return None
                message = messages[message_index]
                message_index += 1
                return message

            def close(self) -> None:
                return None

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 6

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            run_pymavlink_relay(
                "udpin://127.0.0.1:14540",
                destination,
                stale_timeout_s=10.0,
                clock=lambda: clock,
                mavlink_factory=lambda _url: FakeMavlink(),
                should_stop=should_stop,
                monotonic=monotonic,
            )
            document = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(document["captured_at"], "2026-07-22T09:00:00Z")
        self.assertFalse(document["in_air"])


if __name__ == "__main__":
    unittest.main()