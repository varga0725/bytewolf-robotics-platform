import asyncio
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
import unittest

from brain.adapters.offboard_route_executor import (
    OffboardRouteExecutionError,
    OffboardRouteExecutor,
    RouteRunResult,
    RouteState,
)
from brain.control.contract import Velocity
from brain.safety.profile import OffboardLimits, SafetyProfile, ShieldLimits
from brain.telemetry.observation import load_observation


NOW = datetime(2026, 7, 28, 10, 0, tzinfo=UTC)


def _profile() -> SafetyProfile:
    return SafetyProfile(
        vehicle_id="x500v2_reference_01",
        max_altitude_m=20.0,
        max_speed_m_s=0.6,
        max_radius_m=2000.0,
        minimum_battery_percent_to_start=40.0,
        loss_of_link_action="RTL",
        offboard=OffboardLimits(
            enabled=True,
            frame="body_frd",
            max_setpoint_ttl_s=0.4,
            max_rate_hz=10.0,
            max_acceleration_m_s2=2.0,
            max_yaw_rate_deg_s=45.0,
            max_uncertainty_m_s=1.0,
            watchdog_timeout_s=0.8,
            fallback_sequence=("zero_velocity", "hold", "land"),
        ),
        shield=ShieldLimits(
            enabled=True,
            minimum_clearance_m=2.0,
            braking_deceleration_m_s2=1.0,
            reaction_latency_s=0.2,
            max_observation_age_s=0.5,
            unobserved_is_blocked=True,
        ),
    )


def _observation(*, distance_m: float | None = None, at: datetime = NOW):
    coverage = "clear" if distance_m is None else "measured"
    sector = {"yaw_deg": 0.0, "width_deg": 30.0, "coverage": coverage}
    if distance_m is not None:
        sector["distance_m"] = distance_m
    return load_observation(
        {
            "contract_version": "v0.1",
            "vehicle_id": "x500v2_reference_01",
            "observed_at": at.isoformat().replace("+00:00", "Z"),
            "max_age_s": 1.0,
            "kind": "obstacle",
            "validity": "valid",
            "payload": {
                "frame": "body_frd",
                "sensor": {
                    "id": "lidar_2d_v2",
                    "min_range_m": 0.1,
                    "max_range_m": 30.0,
                },
                "sectors": [sector],
            },
        }
    )


class FakeClock:
    def __init__(self) -> None:
        self.current = NOW
        self.monotonic_s = 0.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.monotonic_s

    async def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.monotonic_s += seconds


class StateSource:
    def __init__(self, states: list[RouteState]) -> None:
        self.states = list(states)
        self.index = 0

    async def sample(self) -> RouteState:
        state = self.states[min(self.index, len(self.states) - 1)]
        self.index += 1
        return state


class FailingStateSource:
    async def sample(self) -> RouteState:
        raise RuntimeError("telemetry stream ended")


class DelayedStateSource:
    def __init__(self, clock: FakeClock, state: RouteState) -> None:
        self.clock = clock
        self.state = state

    async def sample(self) -> RouteState:
        await self.clock.sleep(0.6)
        return self.state


class DelayedSecondStateSource:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.calls = 0

    async def sample(self) -> RouteState:
        self.calls += 1
        if self.calls == 1:
            return _state(5.0)
        await self.clock.sleep(0.5)
        return _state(4.8, at=self.clock.now())


class ObstacleSource:
    def __init__(self, observations) -> None:
        self.observations = list(observations)
        self.index = 0

    def latest(self):
        observation = self.observations[min(self.index, len(self.observations) - 1)]
        self.index += 1
        return observation


class RecordingAdapter:
    def __init__(self, events: list) -> None:
        self.events = events

    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None:
        self.events.append(("send", velocity, frame))

    async def start(self) -> None:
        self.events.append("start")

    async def stop(self) -> None:
        self.events.append("stop")


class AdvancingSendAdapter(RecordingAdapter):
    def __init__(self, events: list, clock: FakeClock) -> None:
        super().__init__(events)
        self.clock = clock

    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None:
        await self.clock.sleep(0.5)
        await super().send_velocity_async(velocity, frame)


class RecordingFallback:
    def __init__(self, events: list) -> None:
        self.events = events
        self.calls = []

    async def execute(self, sequence):
        from brain.adapters.offboard_fallback import FallbackRecord

        self.calls.append(tuple(sequence))
        self.events.append(("fallback", tuple(sequence)))
        return FallbackRecord(completed=list(sequence))


class BlockingFallback:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def execute(self, sequence):
        from brain.adapters.offboard_fallback import FallbackRecord

        self.calls += 1
        self.started.set()
        await self.release.wait()
        return FallbackRecord(completed=list(sequence))


def _state(north_error_m: float, *, at: datetime = NOW) -> RouteState:
    return RouteState(
        north_error_m=north_error_m,
        east_error_m=0.0,
        altitude_error_m=0.0,
        heading_deg=0.0,
        observed_at=at,
    )


class OffboardRouteExecutorTests(unittest.IsolatedAsyncioTestCase):
    def executor(self, states, observations, *, adapter_factory=None, tick_observer=None):
        profile = _profile()
        clock = FakeClock()
        events = []
        adapter = (
            RecordingAdapter(events)
            if adapter_factory is None
            else adapter_factory(events, clock)
        )
        fallback = RecordingFallback(events)
        executor = OffboardRouteExecutor(
            profile=profile,
            adapter=adapter,
            fallback_executor=fallback,
            state_source=StateSource(states),
            obstacle_source=ObstacleSource(observations),
            now=clock.now,
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            stream_hz=5.0,
            telemetry_max_age_s=0.5,
            max_vertical_speed_m_s=0.5,
            slowdown_radius_m=3.0,
            stream_id_factory=lambda: "route-a",
            tick_observer=tick_observer,
        )
        return executor, events, fallback

    async def test_delivers_the_first_setpoint_before_start_and_zero_before_success(self) -> None:
        executor, events, fallback = self.executor(
            [_state(5.0), _state(0.1, at=NOW + timedelta(seconds=0.2))],
            [_observation(), _observation(at=NOW + timedelta(seconds=0.2))],
        )

        result = await executor.run(arrival_tolerance_m=0.5, timeout_s=2.0)

        self.assertTrue(result.reached)
        self.assertEqual(events[0][0], "send")
        self.assertEqual(events[1], "start")
        self.assertEqual(events[2][0], "send")
        self.assertEqual(events[2][1], Velocity(0.0, 0.0, 0.0, 0.0))
        self.assertEqual(events[-1], "stop")
        self.assertEqual(fallback.calls, [])

    async def test_tick_observer_records_the_actual_delivery_outcome(self) -> None:
        ticks = []
        executor, _events, _fallback = self.executor(
            [_state(0.1)],
            [_observation()],
            tick_observer=ticks.append,
        )

        await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        self.assertEqual(len(ticks), 1)
        self.assertTrue(ticks[0].delivered)
        self.assertEqual(ticks[0].commanded_velocity.speed_m_s, 0.0)
        self.assertEqual(ticks[0].verdict, "clear")

    async def test_active_obstacle_delivers_an_immediate_zero_then_times_out_safely(self) -> None:
        executor, events, fallback = self.executor(
            [_state(5.0)],
            [_observation(distance_m=1.0)],
        )

        with self.assertRaises(OffboardRouteExecutionError) as caught:
            await executor.run(arrival_tolerance_m=0.5, timeout_s=0.4)

        sent = [event[1] for event in events if isinstance(event, tuple) and event[0] == "send"]
        self.assertTrue(sent)
        self.assertTrue(all(velocity.speed_m_s == 0.0 for velocity in sent))
        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])
        self.assertIsNotNone(caught.exception.fallback)
        self.assertEqual(caught.exception.reason.value, "timeout")

    async def test_await_crossing_route_deadline_is_classified_as_route_timeout(self) -> None:
        executor, _events, _fallback = self.executor(
            [_state(5.0)],
            [_observation(distance_m=1.0)],
        )
        clock = executor._now.__self__

        async def oversleep(_seconds: float) -> None:
            await clock.sleep(0.3)

        executor._sleep = oversleep

        with self.assertRaises(OffboardRouteExecutionError) as caught:
            await executor.run(arrival_tolerance_m=0.5, timeout_s=0.2)

        self.assertEqual(caught.exception.reason.value, "timeout")
        self.assertIsNotNone(caught.exception.fallback)

    async def test_missing_observation_can_deliver_only_zero(self) -> None:
        executor, events, fallback = self.executor([_state(5.0)], [None])

        with self.assertRaises(OffboardRouteExecutionError):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=0.2)

        sent = [event[1] for event in events if isinstance(event, tuple) and event[0] == "send"]
        self.assertTrue(sent)
        self.assertTrue(all(velocity.speed_m_s == 0.0 for velocity in sent))
        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])

    async def test_clear_motion_then_obstacle_crosses_the_real_boundary_as_zero(self) -> None:
        executor, events, _fallback = self.executor(
            [_state(5.0), _state(4.8, at=NOW + timedelta(seconds=0.2))],
            [_observation(), _observation(distance_m=1.0, at=NOW + timedelta(seconds=0.2))],
        )

        with self.assertRaises(OffboardRouteExecutionError):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=0.4)

        sent = [event[1] for event in events if isinstance(event, tuple) and event[0] == "send"]
        self.assertGreater(sent[0].speed_m_s, 0.0)
        self.assertEqual(sent[1].speed_m_s, 0.0)

    async def test_a_temporary_obstacle_can_clear_without_an_acceleration_rejection(self) -> None:
        executor, events, fallback = self.executor(
            [
                _state(5.0),
                _state(4.8, at=NOW + timedelta(seconds=0.2)),
                _state(4.6, at=NOW + timedelta(seconds=0.4)),
                _state(0.1, at=NOW + timedelta(seconds=0.6)),
            ],
            [
                _observation(distance_m=1.0),
                _observation(at=NOW + timedelta(seconds=0.2)),
                _observation(at=NOW + timedelta(seconds=0.4)),
                _observation(at=NOW + timedelta(seconds=0.6)),
            ],
        )

        result = await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        sent = [event[1] for event in events if isinstance(event, tuple) and event[0] == "send"]
        self.assertTrue(result.reached)
        self.assertEqual(sent[0].speed_m_s, 0.0)
        self.assertAlmostEqual(sent[1].speed_m_s, 0.3996)
        self.assertAlmostEqual(sent[2].speed_m_s, 0.5994)
        self.assertEqual(sent[-1].speed_m_s, 0.0)
        self.assertEqual(fallback.calls, [])

    async def test_slew_uses_actual_tick_elapsed_time(self) -> None:
        executor, events, _fallback = self.executor(
            [
                _state(5.0),
                _state(4.9, at=NOW + timedelta(seconds=0.1)),
                _state(0.1, at=NOW + timedelta(seconds=0.2)),
            ],
            [
                _observation(),
                _observation(at=NOW + timedelta(seconds=0.1)),
                _observation(at=NOW + timedelta(seconds=0.2)),
            ],
        )
        clock = executor._now.__self__

        async def fast_sleep(_seconds: float) -> None:
            await clock.sleep(0.1)

        executor._sleep = fast_sleep

        result = await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        sent = [
            event[1]
            for event in events
            if isinstance(event, tuple) and event[0] == "send"
        ]
        self.assertTrue(result.reached)
        self.assertAlmostEqual(sent[0].speed_m_s, 0.3996)
        self.assertLessEqual(
            (sent[1].speed_m_s - sent[0].speed_m_s) / 0.1,
            2.0,
        )

    async def test_forward_wall_clock_jump_cannot_expand_slew_budget(self) -> None:
        executor, events, _fallback = self.executor(
            [_state(5.0), _state(4.9, at=NOW + timedelta(seconds=0.5))],
            [
                _observation(),
                _observation(at=NOW + timedelta(seconds=0.5)),
            ],
        )
        clock = executor._now.__self__
        calls = 0

        async def jumped_sleep(_seconds: float) -> None:
            nonlocal calls
            calls += 1
            clock.monotonic_s += 0.1
            clock.current += timedelta(seconds=0.5 if calls == 1 else 0.1)

        executor._sleep = jumped_sleep

        with self.assertRaises(OffboardRouteExecutionError) as caught:
            await executor.run(arrival_tolerance_m=0.5, timeout_s=0.3)

        sent = [
            event[1]
            for event in events
            if isinstance(event, tuple) and event[0] == "send"
        ]
        self.assertGreaterEqual(len(sent), 2, str(caught.exception))
        self.assertLessEqual(sent[1].speed_m_s - sent[0].speed_m_s, 0.2)

    async def test_stale_route_telemetry_fails_closed_and_executes_fallback_once(self) -> None:
        executor, events, fallback = self.executor(
            [_state(5.0, at=NOW - timedelta(seconds=1.0))],
            [_observation()],
        )

        with self.assertRaisesRegex(OffboardRouteExecutionError, "telemetry"):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])
        self.assertFalse(any(event == "start" for event in events))

    async def test_unexpected_source_failure_executes_fallback_once(self) -> None:
        executor, events, fallback = self.executor([_state(5.0)], [_observation()])
        executor._state_source = FailingStateSource()

        with self.assertRaisesRegex(
            OffboardRouteExecutionError, "telemetry stream ended"
        ):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])
        self.assertFalse(any(event == "start" for event in events))

    async def test_telemetry_age_is_measured_after_the_source_await(self) -> None:
        executor, events, fallback = self.executor([_state(5.0)], [_observation()])
        clock = executor._now.__self__
        executor._state_source = DelayedStateSource(clock, _state(5.0))

        with self.assertRaisesRegex(OffboardRouteExecutionError, "telemetry"):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])
        self.assertFalse(any(event == "start" for event in events))

    async def test_a_slow_next_sample_cannot_outlive_the_moving_setpoint_ttl(self) -> None:
        executor, events, fallback = self.executor(
            [_state(5.0)], [_observation(), _observation()]
        )
        clock = executor._now.__self__
        executor._state_source = DelayedSecondStateSource(clock)

        with self.assertRaisesRegex(
            OffboardRouteExecutionError, "setpoint deadline"
        ):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=2.0)

        sent = [event[1] for event in events if isinstance(event, tuple) and event[0] == "send"]
        self.assertEqual(len(sent), 1)
        self.assertGreater(sent[0].speed_m_s, 0.0)
        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])

    async def test_a_slow_first_delivery_cannot_extend_ttl_from_completion(self) -> None:
        executor, events, fallback = self.executor(
            [_state(5.0)],
            [_observation()],
            adapter_factory=AdvancingSendAdapter,
        )

        with self.assertRaisesRegex(
            OffboardRouteExecutionError, "setpoint deadline"
        ):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=2.0)

        self.assertFalse(any(event == "start" for event in events))
        self.assertEqual(fallback.calls, [("zero_velocity", "hold", "land")])

    async def test_cancellation_waits_for_the_single_in_progress_fallback(self) -> None:
        executor, _events, _fallback = self.executor(
            [_state(5.0, at=NOW - timedelta(seconds=1.0))],
            [_observation()],
        )
        blocking = BlockingFallback()
        executor._fallback_executor = blocking
        task = asyncio.create_task(
            executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)
        )
        await blocking.started.wait()

        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        blocking.release.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(blocking.calls, 1)

    async def test_blocking_fallback_is_bounded(self) -> None:
        executor, _events, _fallback = self.executor(
            [_state(5.0, at=NOW - timedelta(seconds=1.0))],
            [_observation()],
        )
        blocking = BlockingFallback()
        executor._fallback_executor = blocking
        executor._fallback_timeout_s = 0.01
        executor._fallback_cancel_grace_s = 0.01

        with self.assertRaisesRegex(
            OffboardRouteExecutionError, "fallback exceeded"
        ):
            await executor.run(arrival_tolerance_m=0.5, timeout_s=1.0)

        self.assertEqual(blocking.calls, 1)

    async def test_result_is_immutable(self) -> None:
        result = RouteRunResult(
            reached=True,
            ticks=1,
            shield_interventions=0,
            final_verdict="clear",
            session_record={},
        )

        with self.assertRaises(FrozenInstanceError):
            result.reached = False


if __name__ == "__main__":
    unittest.main()
