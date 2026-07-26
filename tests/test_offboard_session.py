"""The Offboard session: validation, delivery, and the boundary that isn't crossed.

Two things are being pinned here. The first is behaviour: what reaches the
adapter, what does not, and what happens when the adapter breaks. The second is
architecture -- the static tests at the bottom assert that this package cannot
arm, cannot change mode, and cannot reach an actuator, which is a property no
behavioural test can establish because it is about what the code *cannot* do.

Shadow mode gets the most attention because it is the default, and because the
whole point of it is that a fully exercised session delivers nothing at all.
"""

from datetime import UTC, datetime, timedelta
from pathlib import Path
import unittest

from brain.control.boundary import RejectionReason
from brain.control.contract import Velocity, load_setpoint
from brain.control.session import OffboardAdapterError, OffboardSession
from brain.control.watchdog import StreamState
from brain.safety.profile import OffboardLimits, SafetyProfile


NOW = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def _limits(**overrides) -> OffboardLimits:
    values = {
        "enabled": True,
        "frame": "local_ned",
        "max_setpoint_ttl_s": 0.5,
        "max_rate_hz": 50.0,
        "max_acceleration_m_s2": 2.0,
        "max_yaw_rate_deg_s": 45.0,
        "max_uncertainty_m_s": 1.0,
        "watchdog_timeout_s": 1.0,
        "fallback_sequence": ("zero_velocity", "hold", "land"),
    }
    values.update(overrides)
    return OffboardLimits(**values)


def _profile(offboard: OffboardLimits | None = None) -> SafetyProfile:
    return SafetyProfile(
        vehicle_id="x500v2_reference_01",
        max_altitude_m=20.0,
        max_speed_m_s=3.0,
        max_radius_m=2000.0,
        minimum_battery_percent_to_start=40.0,
        loss_of_link_action="RTL",
        offboard=offboard if offboard is not None else _limits(),
    )


def _setpoint(*, at: datetime = NOW, sequence: int = 1, **overrides):
    document = {
        "contract_version": "v0.1",
        "vehicle_id": "x500v2_reference_01",
        "stream_id": "stream-a",
        "sequence": sequence,
        "issued_at": at.isoformat().replace("+00:00", "Z"),
        "ttl_s": 0.2,
        "frame": "local_ned",
        "kind": "velocity",
        "validity": "valid",
        "velocity": {"x_m_s": 1.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0},
    }
    document.update(overrides)
    return load_setpoint(document)


class _RecordingAdapter:
    def __init__(self, fail_with: str | None = None):
        self.sent: list[tuple[Velocity, str]] = []
        self._fail_with = fail_with

    def send_velocity(self, velocity: Velocity, frame: str) -> None:
        if self._fail_with is not None:
            raise OffboardAdapterError(self._fail_with)
        self.sent.append((velocity, frame))


class ShadowModeTests(unittest.TestCase):
    def test_a_shadow_session_validates_everything_and_delivers_nothing(self) -> None:
        adapter = _RecordingAdapter()
        session = OffboardSession(_profile(), adapter)

        outcome = session.offer(_setpoint(), NOW)

        self.assertTrue(outcome.approved, "the setpoint is inside the envelope")
        self.assertFalse(outcome.delivered)
        self.assertEqual(adapter.sent, [], "shadow mode must not reach the adapter")

    def test_shadow_is_the_default(self) -> None:
        self.assertTrue(OffboardSession(_profile()).shadow)

    def test_a_shadow_session_still_records_the_evidence(self) -> None:
        # The point of shadow mode: the rejection rates and reasons the roadmap
        # asks for, produced without a command leaving the process.
        session = OffboardSession(_profile())
        session.offer(_setpoint(sequence=1), NOW)
        session.offer(
            _setpoint(sequence=2, at=NOW, velocity={"x_m_s": 9.0, "y_m_s": 0.0, "z_m_s": 0.0, "yaw_rate_deg_s": 0.0}),
            NOW + timedelta(seconds=0.1),
        )

        document = session.record.as_document()

        self.assertEqual(document["offered"], 2)
        self.assertEqual(document["approved"], 1)
        self.assertEqual(document["delivered"], 0)
        self.assertEqual(document["rejections"], {RejectionReason.SPEED_TOO_HIGH.value: 1})


class ActivationTests(unittest.TestCase):
    def test_an_active_session_needs_the_twin_to_agree(self) -> None:
        # Two independent switches: the twin is the vehicle's policy, the
        # argument is this run's intent. Neither alone may go live.
        with self.assertRaises(ValueError):
            OffboardSession(_profile(_limits(enabled=False)), _RecordingAdapter(), shadow=False)

    def test_an_active_session_needs_an_adapter(self) -> None:
        with self.assertRaises(ValueError):
            OffboardSession(_profile(), None, shadow=False)

    def test_an_active_session_delivers_an_approved_setpoint(self) -> None:
        adapter = _RecordingAdapter()
        session = OffboardSession(_profile(), adapter, shadow=False)

        outcome = session.offer(_setpoint(), NOW)

        self.assertTrue(outcome.delivered)
        self.assertEqual(len(adapter.sent), 1)
        velocity, frame = adapter.sent[0]
        self.assertEqual(velocity.x_m_s, 1.0)
        self.assertEqual(frame, "local_ned")

    def test_a_rejected_setpoint_never_reaches_the_adapter(self) -> None:
        adapter = _RecordingAdapter()
        session = OffboardSession(_profile(), adapter, shadow=False)

        session.offer(_setpoint(frame="body_frd"), NOW)

        self.assertEqual(adapter.sent, [])


class WatchdogIntegrationTests(unittest.TestCase):
    def test_a_rejected_setpoint_does_not_keep_the_stream_alive(self) -> None:
        # Otherwise a producer flooding the boundary with refused commands would
        # look like a healthy stream while commanding nothing at all.
        session = OffboardSession(_profile())
        session.offer(_setpoint(sequence=1), NOW)

        much_later = NOW + timedelta(seconds=1.5)
        session.offer(_setpoint(sequence=2, at=much_later, frame="body_frd"), much_later)

        self.assertIs(session.poll(much_later).state, StreamState.STOPPED)

    def test_silence_produces_the_twin_s_fallback(self) -> None:
        session = OffboardSession(_profile())
        session.offer(_setpoint(), NOW)

        decision = session.poll(NOW + timedelta(seconds=2))

        self.assertIs(decision.state, StreamState.STOPPED)
        self.assertEqual(decision.fallback, ("zero_velocity", "hold", "land"))
        self.assertEqual(session.record.fallbacks, ["zero_velocity"])

    def test_a_fallback_is_recorded_once_not_once_per_poll(self) -> None:
        # A control loop polling at 20 Hz would otherwise write the same event
        # until the artifact was useless.
        session = OffboardSession(_profile())
        session.offer(_setpoint(), NOW)
        for tick in range(10):
            session.poll(NOW + timedelta(seconds=2 + tick * 0.05))

        self.assertEqual(session.record.fallbacks, ["zero_velocity"])

    def test_an_adapter_failure_becomes_a_fallback_not_an_exception(self) -> None:
        session = OffboardSession(_profile(), _RecordingAdapter(fail_with="link closed"), shadow=False)

        outcome = session.offer(_setpoint(), NOW)

        self.assertTrue(outcome.approved, "the boundary approved it before the adapter broke")
        self.assertFalse(outcome.delivered)
        self.assertIs(outcome.watchdog.state, StreamState.STOPPED)
        self.assertEqual(session.record.fallbacks, ["zero_velocity"])

    def test_a_fallback_resets_the_stream_so_a_restart_proves_itself(self) -> None:
        session = OffboardSession(_profile())
        session.offer(_setpoint(stream_id="stream-a", sequence=50), NOW)
        session.poll(NOW + timedelta(seconds=2))

        resumed_at = NOW + timedelta(seconds=3)
        outcome = session.offer(
            _setpoint(stream_id="stream-b", sequence=1, at=resumed_at), resumed_at
        )

        self.assertTrue(outcome.approved, "a new stream may start after the old one died")


class ControlBoundaryTests(unittest.TestCase):
    """What this package cannot do, asserted statically.

    A behavioural test cannot show the absence of a capability. These read the
    source instead, the same way the vision and speech packages lock their own
    boundaries.
    """

    CONTROL_SOURCES = tuple(sorted((ROOT / "brain/control").glob("*.py")))

    #: Names no control-boundary source may call or define. Matched against the
    #: parsed syntax tree rather than the raw text, so the docstrings that
    #: explain why these are absent do not themselves trip the check.
    FORBIDDEN_NAMES = frozenset(
        {
            "arm", "disarm", "kill", "set_mode", "set_actuator", "set_actuator_control",
            "actuator", "motor", "set_motor", "takeoff", "land", "return_to_launch",
            "goto_location", "do_orbit", "reboot", "shutdown",
        }
    )

    def test_the_package_calls_or_defines_no_arm_mode_or_actuator_api(self) -> None:
        import ast

        self.assertTrue(self.CONTROL_SOURCES, "the control package has no sources")
        for source in self.CONTROL_SOURCES:
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            names: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    names.add(node.attr)
                elif isinstance(node, ast.Name):
                    names.add(node.id)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    names.add(node.name)
            offending = sorted(names & self.FORBIDDEN_NAMES)
            with self.subTest(source=source.name):
                self.assertEqual(offending, [], f"{source.name} reaches {offending}")

    def test_the_package_never_imports_mavsdk_or_the_mission_path(self) -> None:
        # The adapter is a Protocol the caller satisfies. Importing MAVSDK here
        # would put the whole of its API one attribute access away.
        for source in self.CONTROL_SOURCES:
            text = source.read_text(encoding="utf-8").lower()
            for needle in ("mavsdk", "pymavlink", "mavlink", "brain.mission", "brain.adapters"):
                with self.subTest(source=source.name, needle=needle):
                    self.assertNotIn(f"import {needle}", text)
                    self.assertNotIn(f"from {needle}", text)

    def test_the_read_only_telemetry_bridge_gains_no_control_path(self) -> None:
        # The roadmap's explicit warning: the telemetry-only bridge must not
        # quietly become a control path.
        for package in ("brain/telemetry", "robots/drone/x500v2/ros2", "apps/dashboard"):
            for source in sorted((ROOT / package).glob("**/*.py")):
                with self.subTest(source=str(source.relative_to(ROOT))):
                    self.assertNotIn(
                        "brain.control", source.read_text(encoding="utf-8"),
                        "a read-only path must not reach the Offboard boundary",
                    )

    def test_the_shipped_twin_keeps_offboard_disabled(self) -> None:
        from brain.safety.profile import load_safety_profile

        offboard = load_safety_profile().offboard
        assert offboard is not None
        self.assertFalse(offboard.enabled)


if __name__ == "__main__":
    unittest.main()
