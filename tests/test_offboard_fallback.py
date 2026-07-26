"""The fallback executor: the thing that makes a decided fallback happen.

Introduced because a SITL run proved the decision alone was not enough. MAVSDK
re-sends the last Offboard setpoint at 20 Hz of its own accord, so a producer
going silent leaves PX4 seeing a healthy stream -- and the vehicle kept flying
the last command through twenty seconds of silence.

The ordering and the per-step error handling are what these tests pin, because
both were chosen against specific failures rather than by convention.
"""

from __future__ import annotations

import unittest

from brain.adapters.offboard_fallback import (
    FallbackExecutionError,
    OffboardFallbackExecutor,
)
from brain.control.contract import Velocity


class _Action:
    def __init__(self, fail: set[str] | None = None):
        self.calls: list[str] = []
        self._fail = fail or set()

    async def hold(self) -> None:
        self._record("hold")

    async def land(self) -> None:
        self._record("land")

    async def return_to_launch(self) -> None:
        self._record("rtl")

    def _record(self, name: str) -> None:
        if name in self._fail:
            raise RuntimeError(f"{name} denied")
        self.calls.append(name)


class _System:
    def __init__(self, fail: set[str] | None = None):
        self.action = _Action(fail)


class _Adapter:
    def __init__(self, fail: set[str] | None = None):
        self.sent: list[Velocity] = []
        self.stopped = 0
        self._fail = fail or set()

    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None:
        if "send" in self._fail:
            raise RuntimeError("setpoint denied")
        self.sent.append(velocity)

    async def stop(self) -> None:
        if "stop" in self._fail:
            raise RuntimeError("stop denied")
        self.stopped += 1


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_it_stops_the_vehicle_before_anything_else(self) -> None:
        system, adapter = _System(), _Adapter()

        record = await OffboardFallbackExecutor(system, adapter).execute(
            ["zero_velocity", "hold", "land"]
        )

        self.assertEqual(record.completed, ["zero_velocity", "hold", "land"])
        self.assertTrue(record.stopped_the_vehicle)
        zero = adapter.sent[0]
        self.assertEqual((zero.x_m_s, zero.y_m_s, zero.z_m_s, zero.yaw_rate_deg_s), (0.0, 0.0, 0.0, 0.0))

    async def test_hold_leaves_offboard_so_the_resend_actually_stops(self) -> None:
        # Leaving Offboard is what ends MAVSDK's 20 Hz re-send. Without it every
        # later step competes with a stream still telling PX4 what to do.
        system, adapter = _System(), _Adapter()

        await OffboardFallbackExecutor(system, adapter).execute(["zero_velocity", "hold"])

        self.assertEqual(adapter.stopped, 1)
        self.assertEqual(system.action.calls, ["hold"])

    async def test_rtl_is_available_when_a_twin_asks_for_it(self) -> None:
        system, adapter = _System(), _Adapter()

        await OffboardFallbackExecutor(system, adapter).execute(["zero_velocity", "rtl"])

        self.assertEqual(system.action.calls, ["rtl"])


class PartialFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_failed_step_does_not_abandon_the_rest(self) -> None:
        # The sequence is ordered by how much it gives up, so a later step must
        # still be attempted. Stopping at the first failure would leave the
        # vehicle holding a velocity because one mode change happened to error.
        system, adapter = _System(fail={"hold"}), _Adapter()

        record = await OffboardFallbackExecutor(system, adapter).execute(
            ["zero_velocity", "hold", "land"]
        )

        self.assertEqual(record.completed, ["zero_velocity", "land"])
        self.assertIn("hold", record.failed)
        self.assertEqual(system.action.calls, ["land"])

    async def test_a_failed_stop_is_reported_not_hidden(self) -> None:
        system, adapter = _System(), _Adapter(fail={"send"})

        record = await OffboardFallbackExecutor(system, adapter).execute(["zero_velocity", "land"])

        self.assertFalse(record.stopped_the_vehicle)
        self.assertIn("zero_velocity", record.failed)
        self.assertEqual(record.completed, ["land"])

    async def test_the_record_serialises_for_the_artifact(self) -> None:
        system, adapter = _System(fail={"land"}), _Adapter()

        record = await OffboardFallbackExecutor(system, adapter).execute(["zero_velocity", "land"])

        self.assertEqual(
            record.as_document(),
            {"completed": ["zero_velocity"], "failed": {"land": "land denied"}},
        )


class UnknownStepTests(unittest.IsolatedAsyncioTestCase):
    async def test_a_step_it_cannot_perform_is_refused_before_anything_runs(self) -> None:
        # Skipping an unknown step would leave the vehicle in a state nobody
        # chose, and would do it quietly.
        system, adapter = _System(), _Adapter()

        with self.assertRaises(FallbackExecutionError):
            await OffboardFallbackExecutor(system, adapter).execute(["zero_velocity", "improvise"])

        self.assertEqual(adapter.sent, [], "nothing may run before the sequence is understood")

    async def test_every_step_the_twin_may_declare_is_executable(self) -> None:
        # The profile loader accepts these four; the executor must implement
        # exactly them, or a valid twin could ask for something impossible.
        from brain.adapters.offboard_fallback import KNOWN_STEPS
        from brain.safety.profile import _FALLBACK_STEPS  # noqa: PLC2701 - the paired allowlist

        self.assertEqual(KNOWN_STEPS, _FALLBACK_STEPS)


if __name__ == "__main__":
    unittest.main()
