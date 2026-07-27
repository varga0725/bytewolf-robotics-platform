"""The MAVSDK velocity adapter: the narrow thing that carries an approved setpoint.

No flight stack here. The MAVSDK system and its offboard module are both
injected, so these run anywhere. What is asserted is the mapping (unit for unit,
no arithmetic), the refusal of a frame it cannot carry faithfully, and that
failures become ``OffboardAdapterError`` rather than escaping as whatever MAVSDK
happened to raise.
"""

from __future__ import annotations

from pathlib import Path
import unittest

from brain.adapters.offboard_velocity_adapter import SUPPORTED_FRAME, MavsdkVelocityAdapter
from brain.control.contract import Velocity
from brain.control.session import OffboardAdapterError
from brain.safety.profile import load_safety_profile


ROOT = Path(__file__).resolve().parents[1]


class _VelocityBodyYawspeed:
    def __init__(self, forward_m_s, right_m_s, down_m_s, yawspeed_deg_s):
        self.forward_m_s = forward_m_s
        self.right_m_s = right_m_s
        self.down_m_s = down_m_s
        self.yawspeed_deg_s = yawspeed_deg_s


class _OffboardModule:
    VelocityBodyYawspeed = _VelocityBodyYawspeed


class _Offboard:
    def __init__(self, fail_on: str | None = None):
        self.sent: list[_VelocityBodyYawspeed] = []
        self.started = 0
        self.stopped = 0
        self._fail_on = fail_on

    async def set_velocity_body(self, setpoint):
        if self._fail_on == "send":
            raise RuntimeError("COMMAND_DENIED")
        self.sent.append(setpoint)

    async def start(self):
        if self._fail_on == "start":
            raise RuntimeError("no setpoint stream")
        self.started += 1

    async def stop(self):
        if self._fail_on == "stop":
            raise RuntimeError("already out of offboard")
        self.stopped += 1


class _System:
    def __init__(self, fail_on: str | None = None):
        self.offboard = _Offboard(fail_on)


def _adapter(fail_on: str | None = None) -> tuple[MavsdkVelocityAdapter, _System]:
    system = _System(fail_on)
    return MavsdkVelocityAdapter(system, offboard_module=_OffboardModule()), system


class MappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_mapping_is_unit_for_unit_with_no_arithmetic(self) -> None:
        # A conversion is a place a sign can be lost. Both sides already speak
        # forward/right/down in m/s and a yaw rate in deg/s.
        adapter, system = _adapter()

        await adapter.send_velocity_async(Velocity(1.5, -0.5, 0.25, 12.0), SUPPORTED_FRAME)

        sent = system.offboard.sent[0]
        self.assertEqual(sent.forward_m_s, 1.5)
        self.assertEqual(sent.right_m_s, -0.5)
        self.assertEqual(sent.down_m_s, 0.25)
        self.assertEqual(sent.yawspeed_deg_s, 12.0)

    async def test_down_stays_positive_downward(self) -> None:
        # Both conventions agree, so the adapter must not "helpfully" flip it.
        adapter, system = _adapter()

        await adapter.send_velocity_async(Velocity(0.0, 0.0, 0.8, 0.0), SUPPORTED_FRAME)

        self.assertEqual(system.offboard.sent[0].down_m_s, 0.8)


class FrameTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_frame_it_cannot_carry_faithfully_is_refused(self) -> None:
        # MAVSDK's NED velocity setpoint takes a yaw *angle*; the contract
        # carries a yaw *rate*. Serving local_ned would mean silently
        # reinterpreting one as the other inside a movement command.
        adapter, system = _adapter()

        with self.assertRaises(OffboardAdapterError) as raised:
            await adapter.send_velocity_async(Velocity(1.0, 0.0, 0.0, 0.0), "local_ned")

        self.assertIn("yaw", str(raised.exception))
        self.assertEqual(system.offboard.sent, [], "nothing may be sent for a refused frame")

    def test_the_shipped_twin_declares_the_frame_this_adapter_serves(self) -> None:
        # The drift this catches: a twin asking for a frame nothing can deliver
        # would fail only at the first real flight.
        offboard = load_safety_profile().offboard
        assert offboard is not None
        self.assertEqual(offboard.frame, SUPPORTED_FRAME)


class FailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_rejected_setpoint_becomes_a_contract_failure(self) -> None:
        # MAVSDK raises its own OffboardError; the session only knows how to
        # handle OffboardAdapterError, and an unhandled type would escape the
        # fallback path entirely.
        adapter, _system = _adapter(fail_on="send")

        with self.assertRaises(OffboardAdapterError):
            await adapter.send_velocity_async(Velocity(1.0, 0.0, 0.0, 0.0), SUPPORTED_FRAME)

    async def test_a_refused_mode_entry_becomes_a_contract_failure(self) -> None:
        adapter, _system = _adapter(fail_on="start")

        with self.assertRaises(OffboardAdapterError):
            await adapter.start()
        self.assertFalse(adapter.started)

    async def test_stop_never_raises_over_the_original_fault(self) -> None:
        # Teardown runs on the way out of failing flights too; a throwing stop
        # would replace the real failure with its own.
        adapter, _system = _adapter(fail_on="stop")
        adapter._started = True  # noqa: SLF001 - simulating a started session

        await adapter.stop()

        self.assertFalse(adapter.started)


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_start_is_idempotent_and_stop_only_undoes_a_start(self) -> None:
        adapter, system = _adapter()

        await adapter.stop()
        self.assertEqual(system.offboard.stopped, 0, "nothing was started")

        await adapter.start()
        await adapter.start()
        self.assertEqual(system.offboard.started, 1)
        self.assertTrue(adapter.started)

        await adapter.stop()
        self.assertEqual(system.offboard.stopped, 1)
        self.assertFalse(adapter.started)


class SurfaceTests(unittest.TestCase):
    """The adapter must stay the narrow thing it was introduced as."""

    SOURCE = ROOT / "brain/adapters/offboard_velocity_adapter.py"

    def test_it_reaches_no_wider_mavsdk_offboard_api(self) -> None:
        # MAVSDK's Offboard plugin also exposes set_actuator_control,
        # set_attitude, set_attitude_rate and position setpoints. None of them
        # belong behind a boundary whose whole justification is being narrow.
        import ast

        tree = ast.parse(self.SOURCE.read_text(encoding="utf-8"))
        attributes = {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        forbidden = {
            "set_actuator_control", "set_attitude", "set_attitude_rate",
            "set_position_ned", "set_position_global", "set_acceleration_ned",
            "set_position_velocity_ned", "set_position_velocity_acceleration_ned",
            "arm", "disarm", "kill", "action",
        }
        self.assertEqual(sorted(attributes & forbidden), [])

    def test_the_control_boundary_still_cannot_reach_mavsdk(self) -> None:
        # This adapter lives outside brain/control precisely so that the package
        # deciding whether a setpoint may fly cannot reach the API that flies it.
        for source in sorted((ROOT / "brain/control").glob("*.py")):
            text = source.read_text(encoding="utf-8").lower()
            with self.subTest(source=source.name):
                self.assertNotIn("import mavsdk", text)
                self.assertNotIn("from mavsdk", text)


if __name__ == "__main__":
    unittest.main()
