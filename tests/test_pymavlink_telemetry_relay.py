"""Unit tests for the read-only pymavlink dashboard telemetry relay."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from brain.telemetry.mavsdk_relay import DashboardTelemetryState
from brain.mission.acceptance_inflight_px4_mode import px4_auto_rtl_custom_mode
from brain.telemetry.pymavlink_relay import (
    apply_mavlink_message,
    core_message_pending_write,
    heartbeat_pending_write,
    run_pymavlink_relay,
)


class _Message:
    def __init__(self, message_type: str, **fields: float | int) -> None:
        self._type = message_type
        for key, value in fields.items():
            setattr(self, key, value)
        self._src_system = int(fields.get("_src_system", 1))
        self._src_component = int(fields.get("_src_component", 1))

    def get_type(self) -> str:
        return self._type

    def get_srcSystem(self) -> int:
        return self._src_system

    def get_srcComponent(self) -> int:
        return self._src_component


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
        state = DashboardTelemetryState(relay_source="pymavlink")
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)
        state = apply_mavlink_message(
            state,
            _Message("ATTITUDE", yaw=1.57),
            observed_at=now,
        )

        state = apply_mavlink_message(
            state,
            _Message("HOME_POSITION", latitude=474000000, longitude=190500000, altitude=500),
            observed_at=now,
        )
        document = state.as_dashboard_document(now)

        self.assertEqual(document["source"], "pymavlink")
        self.assertIn("home", document)
        self.assertAlmostEqual(document["home"]["latitude_deg"], 47.4)
        self.assertAlmostEqual(document["position"]["latitude_deg"], 47.4)
        self.assertAlmostEqual(document["battery"]["remaining_percent"], 88.0)
        self.assertEqual(document["battery"]["observed_at"], "2026-07-22T09:00:00Z")
        self.assertTrue(document["in_air"])
        self.assertIn("heading_deg", document)
        self.assertEqual(document["captured_at"], "2026-07-22T09:00:00Z")


class PymavlinkInvalidBatteryTests(unittest.TestCase):
    def test_apply_mavlink_message_ignores_unknown_and_out_of_range_battery(self) -> None:
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState()
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)
        self.assertAlmostEqual(state.battery.remaining_percent, 88.0)  # type: ignore[union-attr]

        for invalid in (-1, 101, 255):
            for message_type in ("SYS_STATUS", "BATTERY_STATUS"):
                with self.subTest(message_type=message_type, remaining=invalid):
                    after = apply_mavlink_message(
                        state,
                        _Message(message_type, battery_remaining=invalid),
                        observed_at=now,
                    )
                    self.assertIs(after.battery, state.battery)
                    self.assertAlmostEqual(after.battery.remaining_percent, 88.0)  # type: ignore[union-attr]

    def test_apply_mavlink_message_accepts_valid_battery_updates(self) -> None:
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState()
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)

        state = apply_mavlink_message(
            state,
            _Message("SYS_STATUS", battery_remaining=72),
            observed_at=now,
        )
        self.assertAlmostEqual(state.battery.remaining_percent, 72.0)  # type: ignore[union-attr]

        state = apply_mavlink_message(
            state,
            _Message("BATTERY_STATUS", battery_remaining=0),
            observed_at=now,
        )
        self.assertAlmostEqual(state.battery.remaining_percent, 0.0)  # type: ignore[union-attr]

    def test_mixed_battery_sequence_keeps_last_valid_percent(self) -> None:
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState()
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)

        sequence = [
            _Message("SYS_STATUS", battery_remaining=75),
            _Message("BATTERY_STATUS", battery_remaining=-1),
            _Message("SYS_STATUS", battery_remaining=105),
            _Message("BATTERY_STATUS", battery_remaining=74),
            _Message("SYS_STATUS", battery_remaining=-1),
        ]
        for message in sequence:
            state = apply_mavlink_message(state, message, observed_at=now)
        self.assertAlmostEqual(state.battery.remaining_percent, 74.0)  # type: ignore[union-attr]

    def test_core_message_pending_write_false_for_ignored_battery_reads(self) -> None:
        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState()
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)

        for invalid in (-1, 200):
            for message_type in ("SYS_STATUS", "BATTERY_STATUS"):
                message = _Message(message_type, battery_remaining=invalid)
                after = apply_mavlink_message(state, message, observed_at=now)
                self.assertFalse(core_message_pending_write(message_type, state, after))

    def test_invalid_battery_alone_does_not_refresh_captured_at(self) -> None:
        timeline = {"t": 0.0}
        base = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        messages = [
            *_complete_core_messages(),
            _Message("SYS_STATUS", battery_remaining=-1),
            _Message("BATTERY_STATUS", battery_remaining=150),
            None,
        ]
        message_index = 0

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    timeline["t"] = 5.0
                    return None
                message = messages[message_index]
                message_index += 1
                timeline["t"] = 0.5 * message_index
                return message

            def close(self) -> None:
                return None

        def monotonic() -> float:
            return timeline["t"]

        def clock() -> datetime:
            return base + timedelta(seconds=timeline["t"])

        writes: list[tuple[str, float]] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            battery = document["battery"]
            assert isinstance(battery, dict)
            writes.append((str(document["captured_at"]), float(battery["remaining_percent"])))

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 20

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=clock,
                    should_stop=should_stop,
                    monotonic=monotonic,
                )

        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0][1], 88.0)


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

        self.assertEqual(len(writes), 1)
        self.assertNotIn("heading_deg", writes[0])

    def test_identical_core_stream_keeps_advancing_captured_at(self) -> None:
        timeline = {"t": 0.0}
        position = _Message(
            "GLOBAL_POSITION_INT",
            lat=474000000,
            lon=190500000,
            alt=120500,
            relative_alt=2500,
        )
        messages = [
            *_complete_core_messages(),
            *([position] * 200),
        ]
        message_index = 0
        base = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    timeline["t"] = 10.0
                    return None
                message = messages[message_index]
                message_index += 1
                timeline["t"] = 0.05 * message_index
                return message

            def close(self) -> None:
                return None

        def monotonic() -> float:
            return timeline["t"]

        def clock() -> datetime:
            return base + timedelta(seconds=timeline["t"])

        writes: list[str] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(str(document["captured_at"]))

        def should_stop() -> bool:
            return timeline["t"] >= 10.0

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=clock,
                    should_stop=should_stop,
                    monotonic=monotonic,
                )

        timestamps = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in writes]
        self.assertGreater(len(timestamps), 10)
        for earlier, later in zip(timestamps, timestamps[1:]):
            self.assertLess(earlier, later)


class PymavlinkHeartbeatTests(unittest.TestCase):
    def test_heartbeat_populates_px4_vehicle_fields(self) -> None:
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError:
            self.skipTest("pymavlink not installed")

        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState(relay_source="pymavlink")
        for message in _complete_core_messages():
            state = apply_mavlink_message(state, message, observed_at=now)
        before = state
        custom_mode = px4_auto_rtl_custom_mode()
        heartbeat = _Message(
            "HEARTBEAT",
            type=2,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_PX4,
            base_mode=mavutil.mavlink.MAV_MODE_FLAG_SAFETY_ARMED,
            custom_mode=custom_mode,
        )
        state = apply_mavlink_message(state, heartbeat, observed_at=now)
        self.assertTrue(state.armed)
        self.assertEqual(state.sysid, 1)
        self.assertEqual(state.autopilot, "px4")
        self.assertEqual(state.flight_mode, "return_to_launch")
        self.assertEqual(custom_mode, 0x05040000)
        self.assertTrue(heartbeat_pending_write(before, state))
        self.assertFalse(core_message_pending_write("HEARTBEAT", before, state))

    def test_gcs_heartbeat_is_ignored(self) -> None:
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError:
            self.skipTest("pymavlink not installed")

        now = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        state = DashboardTelemetryState(relay_source="pymavlink")
        heartbeat = _Message(
            "HEARTBEAT",
            type=6,
            autopilot=mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            base_mode=0,
            custom_mode=0,
        )
        after = apply_mavlink_message(state, heartbeat, observed_at=now)
        self.assertIs(after, state)

    def test_heartbeat_does_not_schedule_disk_write_without_core_change(self) -> None:
        try:
            from pymavlink import mavutil
        except ModuleNotFoundError:
            self.skipTest("pymavlink not installed")

        timeline = {"t": 0.0}
        base = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        messages = [
            *_complete_core_messages(),
            _Message(
                "HEARTBEAT",
                type=6,
                autopilot=mavutil.mavlink.MAV_AUTOPILOT_INVALID,
                base_mode=0,
                custom_mode=0,
            ),
            None,
        ]
        message_index = 0

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    timeline["t"] = 5.0
                    return None
                message = messages[message_index]
                message_index += 1
                timeline["t"] = 0.5 * message_index
                return message

            def close(self) -> None:
                return None

        writes: list[str] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(str(document["captured_at"]))

        def monotonic() -> float:
            return timeline["t"]

        def clock() -> datetime:
            return base + timedelta(seconds=timeline["t"])

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 10

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=clock,
                    should_stop=should_stop,
                    monotonic=monotonic,
                )

        self.assertEqual(len(writes), 1)


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

        self.assertEqual(len(writes), 1)
        self.assertEqual(writes[0], "2026-07-22T09:00:00Z")

    def test_shutdown_after_silence_does_not_write_a_fresh_captured_at(self) -> None:
        timeline = {"t": 0.0}
        base = datetime(2026, 7, 22, 9, 0, tzinfo=UTC)
        messages = [*_complete_core_messages()]
        message_index = 0

        class FakeMavlink:
            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index < len(messages):
                    message = messages[message_index]
                    message_index += 1
                    timeline["t"] = 0.05 * message_index
                    return message
                timeline["t"] = 10.0
                return None

            def close(self) -> None:
                return None

        def monotonic() -> float:
            return timeline["t"]

        def clock() -> datetime:
            return base + timedelta(seconds=timeline["t"])

        writes: list[str] = []

        def recording_write(destination: Path, document: dict[str, object]) -> None:
            writes.append(str(document["captured_at"]))

        stop_after = [0]

        def should_stop() -> bool:
            stop_after[0] += 1
            return stop_after[0] > 25

        with TemporaryDirectory() as directory:
            destination = Path(directory) / "live-telemetry.json"
            with mock.patch("brain.telemetry.pymavlink_relay.write_atomic_json", recording_write):
                run_pymavlink_relay(
                    "udpin://127.0.0.1:14540",
                    destination,
                    write_interval_s=0.2,
                    mavlink_factory=lambda _url: FakeMavlink(),
                    clock=clock,
                    should_stop=should_stop,
                    monotonic=monotonic,
                )

        captured_values = {item for item in writes}
        self.assertEqual(len(captured_values), 1)
        self.assertGreaterEqual(len(writes), 1)


class PymavlinkVehicleIdentitySnapshotTests(unittest.TestCase):
    def test_relay_writes_vehicle_identity_for_gz_params(self) -> None:
        from brain.preflight_params.param_value import encode_px4_int32_wire_value

        class _Hb:
            _type = "HEARTBEAT"
            type = 2
            autopilot = 12
            base_mode = 0
            custom_mode = 0

            def get_type(self) -> str:
                return self._type

            def get_srcSystem(self) -> int:
                return 1

            def get_srcComponent(self) -> int:
                return 1

        class _Pv:
            def __init__(self, name: str, value: int) -> None:
                self._type = "PARAM_VALUE"
                self.param_id = name
                self.param_value = encode_px4_int32_wire_value(value)
                self.param_type = 6

            def get_type(self) -> str:
                return self._type

            def get_srcSystem(self) -> int:
                return 1

            def get_srcComponent(self) -> int:
                return 1

        messages = [
            _Hb(),
            _Pv("SYS_AUTOSTART", 4001),
            _Pv("SIM_GZ_EN", 1),
            *_complete_core_messages(),
            None,
        ]
        message_index = 0

        class FakeMavlink:
            class _Mav:
                @staticmethod
                def param_request_read_send(*_args: object, **_kwargs: object) -> None:
                    return None

            mav = _Mav()

            def recv_match(self, *, blocking: bool, timeout: float) -> object | None:
                nonlocal message_index
                if message_index >= len(messages):
                    return None
                message = messages[message_index]
                message_index += 1
                return message

            def close(self) -> None:
                return None

        writes: list[dict[str, object]] = []

        def recording_write(_destination: Path, document: dict[str, object]) -> None:
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
                    mavlink_factory=lambda _url: FakeMavlink(),
                    should_stop=should_stop,
                )

        self.assertGreaterEqual(len(writes), 1)
        identity = writes[-1].get("vehicle_identity")
        self.assertIsInstance(identity, dict)
        assert isinstance(identity, dict)
        self.assertEqual(identity.get("sys_autostart"), 4001)
        self.assertEqual(identity.get("sim_gz_en"), 1)
        self.assertEqual(identity.get("mavlink_sysid"), 1)

    def test_gcs_heartbeat_does_not_retarget_param_lock(self) -> None:
        from brain.preflight_params.param_value import encode_px4_int32_wire_value
        from brain.telemetry.autopilot_identity import qualifying_autopilot_heartbeat

        class _GcsHb:
            type = 6
            autopilot = 0
            base_mode = 0
            custom_mode = 0

            def get_srcSystem(self) -> int:
                return 255

            def get_srcComponent(self) -> int:
                return 0

        self.assertIsNone(qualifying_autopilot_heartbeat(_GcsHb()))

        class _Pv:
            param_id = "SYS_AUTOSTART"
            param_value = encode_px4_int32_wire_value(4001)
            param_type = 6

            def get_srcSystem(self) -> int:
                return 255

            def get_srcComponent(self) -> int:
                return 0

        collector = __import__(
            "brain.telemetry.autopilot_identity", fromlist=["AutopilotIdentityCollector"]
        ).AutopilotIdentityCollector()
        self.assertFalse(collector.apply_param_value(_Pv))


if __name__ == "__main__":
    unittest.main()