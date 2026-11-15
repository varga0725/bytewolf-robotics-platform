"""Unit tests for the read-only pymavlink dashboard telemetry relay."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from brain.telemetry.mavsdk_relay import DashboardTelemetryState
from brain.telemetry.pymavlink_relay import apply_mavlink_message, run_pymavlink_relay


class _Message:
    def __init__(self, message_type: str, **fields: float | int) -> None:
        self._type = message_type
        for key, value in fields.items():
            setattr(self, key, value)

    def get_type(self) -> str:
        return self._type


def _complete_core_messages() -> list[_Message]:
    return [
        _Message(
            "GLOBAL_POSITION_INT",
            lat=474000000,
            lon=190500000,
            alt=120500,
            relative_alt=2500,
        ),
        _Message("SYS_STATUS", battery_remaining=88),
        _Message("EXTENDED_SYS_STATE", landed_state=2),
    ]


class PymavlinkMappingTests(unittest.TestCase):
    def test_messages_build_the_dashboard_snapshot_contract(self) -> None:
        state = DashboardTelemetryState()
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)
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


class PymavlinkWriteThrottleTests(unittest.TestCase):
    def test_rapid_core_messages_coalesce_to_one_throttled_write(self) -> None:
        timeline = {"t": 0.0}

        messages = [
            *_complete_core_messages(),
            _Message("GLOBAL_POSITION_INT", lat=474000001, lon=190500000, alt=120500, relative_alt=2500),
            _Message("GLOBAL_POSITION_INT", lat=474000002, lon=190500000, alt=120500, relative_alt=2500),
            None,
        ]
        message_index = 0

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    timeline["t"] = 0.25
                    return None
                message = messages[message_index]
                message_index += 1
                timeline["t"] = 0.01 * message_index
                return message

            def close(self) -> None:
                return None

        def monotonic() -> float:
            return timeline["t"]

        writes: list[dict[str, object]] = []
        fixed_now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(dict(document))

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 12

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=lambda: fixed_now,
                    should_stop=should_stop,
                    monotonic=monotonic,
                )

        core_writes = [item for item in writes if "position" in item]
        self.assertGreaterEqual(len(core_writes), 2)
        self.assertLess(len(core_writes), len(messages))

    def test_attitude_only_updates_do_not_trigger_disk_writes(self) -> None:
        writes: list[dict[str, object]] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(dict(document))

        messages = [
            *_complete_core_messages(),
            _Message("ATTITUDE", yaw=0.1),
            _Message("ATTITUDE", yaw=0.2),
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
            return stop_after[0] > 8

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    should_stop=should_stop,
                )

        self.assertEqual(len(writes), 2)
        self.assertNotIn("heading_deg", writes[0])
        self.assertIn("heading_deg", writes[1])


class PymavlinkStaleBehaviorTests(unittest.TestCase):
    def test_silence_does_not_rewrite_or_advance_captured_at(self) -> None:
        def clock() -> datetime:
            return datetime(2026, 7, 22, 9, 0, tzinfo=UTC)

        messages = [*_complete_core_messages(), None]
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

        writes: list[str] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(str(document["captured_at"]))

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 5

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=clock,
                    should_stop=should_stop,
                )

        self.assertGreaterEqual(len(writes), 1)
        self.assertLessEqual(len(writes), 2)
        self.assertTrue(all(item == "2026-07-22T09:00:00Z" for item in writes))


if __name__ == "__main__":
    unittest.main()