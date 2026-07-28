"""Execute one local waypoint leg through the shielded Offboard boundary."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from math import hypot, isfinite
from typing import Protocol
import uuid

from brain.adapters.offboard_fallback import FallbackRecord
from brain.control.contract import Velocity, load_setpoint
from brain.control.session import OffboardSession
from brain.control.shield import RuntimeSafetyShield, ShieldMode
from brain.navigation.offboard_route import OffboardRouteError, plan_body_velocity
from brain.safety.profile import SafetyProfile
from brain.telemetry.observation import Observation


_SLEW_NUMERIC_MARGIN = 0.999


class OffboardModeAdapter(Protocol):
    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...


class FallbackExecutor(Protocol):
    async def execute(self, sequence: Sequence[str]) -> FallbackRecord: ...


class RouteStateSource(Protocol):
    async def sample(self) -> "RouteState": ...


class ObstacleSource(Protocol):
    def latest(self) -> Observation | None: ...


@dataclass(frozen=True)
class RouteState:
    """Fresh launch-relative route error and the heading that makes it body-relative."""

    north_error_m: float
    east_error_m: float
    altitude_error_m: float
    heading_deg: float
    observed_at: datetime


@dataclass(frozen=True)
class RouteRunResult:
    reached: bool
    ticks: int
    shield_interventions: int
    final_verdict: str
    session_record: dict[str, object]


@dataclass(frozen=True)
class RouteTick:
    sequence: int
    observed_at: datetime
    nominal_velocity: Velocity
    verdict: str
    commanded_velocity: Velocity
    delivered: bool
    sensed_distance_m: float | None


class RouteTerminalReason(str, Enum):
    TIMEOUT = "timeout"
    WATCHDOG = "watchdog"
    INPUT_REJECTED = "input_rejected"
    DELIVERY_REJECTED = "delivery_rejected"
    OFFBOARD_REFUSED = "offboard_refused"
    INTERNAL_FAILURE = "internal_failure"


class OffboardRouteExecutionError(RuntimeError):
    """A leg ended safely without reaching its target."""

    def __init__(
        self,
        message: str,
        *,
        reason: RouteTerminalReason,
        fallback: FallbackRecord | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.fallback = fallback


class OffboardRouteExecutor:
    """Compose route planning, shield, boundary, adapter and fallback for one leg."""

    def __init__(
        self,
        *,
        profile: SafetyProfile,
        adapter: OffboardModeAdapter,
        fallback_executor: FallbackExecutor,
        state_source: RouteStateSource,
        obstacle_source: ObstacleSource,
        now: Callable[[], datetime],
        monotonic: Callable[[], float],
        sleep: Callable[[float], Awaitable[None]],
        stream_hz: float,
        telemetry_max_age_s: float,
        max_vertical_speed_m_s: float,
        slowdown_radius_m: float,
        fallback_timeout_s: float = 20.0,
        fallback_cancel_grace_s: float = 1.0,
        stream_id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
        tick_observer: Callable[[RouteTick], None] | None = None,
    ) -> None:
        if profile.offboard is None or profile.shield is None:
            raise ValueError("A shielded route needs both offboard and shield twin blocks.")
        if profile.offboard.frame != "body_frd":
            raise ValueError("The shielded route supports the body_frd frame only.")
        if not profile.shield.unobserved_is_blocked:
            raise ValueError(
                "An autonomous shielded route requires unobserved sectors to be blocked."
            )
        if not isfinite(stream_hz) or stream_hz <= 0 or stream_hz > profile.offboard.max_rate_hz:
            raise ValueError("stream_hz must be positive and within the twin rate limit.")
        if 1.0 / stream_hz >= profile.offboard.max_setpoint_ttl_s:
            raise ValueError("The route period must be shorter than the setpoint TTL.")
        if not isfinite(telemetry_max_age_s) or telemetry_max_age_s <= 0:
            raise ValueError("telemetry_max_age_s must be positive and finite.")
        if not isfinite(max_vertical_speed_m_s) or max_vertical_speed_m_s <= 0:
            raise ValueError("max_vertical_speed_m_s must be positive and finite.")
        if not isfinite(slowdown_radius_m) or slowdown_radius_m <= 0:
            raise ValueError("slowdown_radius_m must be positive and finite.")
        if not isfinite(fallback_timeout_s) or fallback_timeout_s <= 0:
            raise ValueError("fallback_timeout_s must be positive and finite.")
        if not isfinite(fallback_cancel_grace_s) or fallback_cancel_grace_s <= 0:
            raise ValueError("fallback_cancel_grace_s must be positive and finite.")
        self._profile = profile
        self._adapter = adapter
        # Construct the policy-bearing components here. Accepting injected
        # instances would allow a strict profile to be paired with weaker shield
        # limits, or a session that delivers to a different adapter than the one
        # whose Offboard mode this executor starts and stops.
        self._shield = RuntimeSafetyShield(profile.shield, mode=ShieldMode.ACTIVE)
        self._session = OffboardSession(profile, adapter, shadow=False)
        self._fallback_executor = fallback_executor
        self._state_source = state_source
        self._obstacle_source = obstacle_source
        self._now = now
        self._monotonic = monotonic
        self._sleep = sleep
        self._stream_hz = stream_hz
        self._telemetry_max_age_s = telemetry_max_age_s
        self._max_vertical_speed_m_s = max_vertical_speed_m_s
        self._slowdown_radius_m = slowdown_radius_m
        self._fallback_timeout_s = fallback_timeout_s
        self._fallback_cancel_grace_s = fallback_cancel_grace_s
        self._stream_id_factory = stream_id_factory
        self._tick_observer = tick_observer
        self._used = False

    async def run(self, *, arrival_tolerance_m: float, timeout_s: float) -> RouteRunResult:
        if not isfinite(timeout_s) or timeout_s <= 0:
            raise ValueError("timeout_s must be positive and finite.")
        if (
            not isfinite(arrival_tolerance_m)
            or arrival_tolerance_m <= 0
            or arrival_tolerance_m >= self._slowdown_radius_m
        ):
            raise ValueError(
                "arrival_tolerance_m must be positive and smaller than slowdown_radius_m."
            )
        if self._used:
            raise RuntimeError("An OffboardRouteExecutor instance runs one leg only.")
        self._used = True
        stream_id = self._stream_id_factory()
        started_at = self._monotonic()
        deadline = started_at + timeout_s
        ticks = 0
        interventions = 0
        final_verdict = "not_evaluated"
        entered_offboard = False
        fallback_task: asyncio.Task[FallbackRecord] | None = None
        last_delivered = Velocity(0.0, 0.0, 0.0, 0.0)
        last_delivered_monotonic: float | None = None
        active_setpoint_deadline: float | None = None

        async def execute_fallback(sequence: Sequence[str]) -> FallbackRecord:
            nonlocal fallback_task
            if fallback_task is None:
                fallback_task = asyncio.create_task(
                    self._fallback_executor.execute(sequence)
                )
            try:
                return await asyncio.wait_for(
                    asyncio.shield(fallback_task),
                    timeout=self._fallback_timeout_s,
                )
            except TimeoutError:
                fallback_task.cancel()
                quiesced = False
                try:
                    await asyncio.wait_for(
                        asyncio.shield(fallback_task),
                        timeout=self._fallback_cancel_grace_s,
                    )
                except asyncio.CancelledError:
                    quiesced = True
                except Exception:
                    quiesced = fallback_task.done()
                raise TimeoutError(
                    "The terminal fallback exceeded its bounded execution time"
                    + (
                        "."
                        if quiesced
                        else " and did not quiesce after cancellation."
                    )
                )

        async def fail(message: str, reason: RouteTerminalReason) -> None:
            assert self._profile.offboard is not None
            try:
                fallback = await execute_fallback(
                    self._profile.offboard.fallback_sequence
                )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                raise OffboardRouteExecutionError(
                    f"{message} Fallback execution also failed: {error}",
                    reason=reason,
                ) from error
            raise OffboardRouteExecutionError(
                message, reason=reason, fallback=fallback
            )

        try:
            while self._monotonic() < deadline:
                try:
                    state = await self._await_before_deadline(
                        self._state_source.sample(),
                        _earlier(deadline, active_setpoint_deadline),
                    )
                    observation = self._obstacle_source.latest()
                    # Awaiting telemetry can consume most of a command's useful
                    # lifetime. Freshness and issuance are judged after every
                    # input has arrived, never against the pre-await clock.
                    now = self._utc_now()
                    issuance_monotonic = self._monotonic()
                    self._require_fresh_state(state, now)
                    nominal = plan_body_velocity(
                        north_error_m=state.north_error_m,
                        east_error_m=state.east_error_m,
                        altitude_error_m=state.altitude_error_m,
                        heading_deg=state.heading_deg,
                        max_horizontal_speed_m_s=(
                            self._profile.max_speed_m_s * _SLEW_NUMERIC_MARGIN
                        ),
                        max_vertical_speed_m_s=self._max_vertical_speed_m_s,
                        arrival_tolerance_m=arrival_tolerance_m,
                        slowdown_radius_m=self._slowdown_radius_m,
                    )
                    nominal_velocity = _slew_velocity(
                        last_delivered,
                        nominal.velocity,
                        self._profile.offboard.max_acceleration_m_s2
                        * _SLEW_NUMERIC_MARGIN
                        * (
                            1.0 / self._stream_hz
                            if last_delivered_monotonic is None
                            else max(
                                issuance_monotonic - last_delivered_monotonic,
                                0.0,
                            )
                        ),
                    )
                except (OffboardRouteError, ValueError, TypeError) as error:
                    await fail(
                        f"Route telemetry or geometry was rejected: {error}",
                        RouteTerminalReason.INPUT_REJECTED,
                    )

                decision = self._shield.evaluate(
                    nominal_velocity, observation, now
                )
                final_verdict = decision.verdict.value
                if decision.verdict.blocks_motion:
                    interventions += 1
                setpoint = load_setpoint(
                    self._setpoint_document(
                        stream_id, ticks, now, decision.velocity
                    )
                )
                candidate_setpoint_deadline = (
                    self._monotonic()
                    + self._profile.offboard.max_setpoint_ttl_s
                )
                offered = self._session.offer(setpoint, now)
                delivered = await self._await_before_deadline(
                    self._session.deliver(offered, self._utc_now()),
                    min(
                        _earlier(deadline, active_setpoint_deadline),
                        candidate_setpoint_deadline,
                    ),
                )
                if self._tick_observer is not None:
                    self._tick_observer(
                        RouteTick(
                            sequence=ticks,
                            observed_at=now,
                            nominal_velocity=nominal_velocity,
                            verdict=decision.verdict.value,
                            commanded_velocity=decision.velocity,
                            delivered=delivered.delivered,
                            sensed_distance_m=decision.clearance_m,
                        )
                    )
                if delivered.watchdog.requires_fallback:
                    fallback = await execute_fallback(
                        delivered.watchdog.fallback
                    )
                    raise OffboardRouteExecutionError(
                        delivered.watchdog.reason,
                        reason=RouteTerminalReason.WATCHDOG,
                        fallback=fallback,
                    )
                if not delivered.approved or not delivered.delivered:
                    await fail(
                        delivered.decision.detail
                        or "The Offboard boundary did not deliver the route setpoint.",
                        RouteTerminalReason.DELIVERY_REJECTED,
                    )
                assert delivered.decision.velocity is not None
                last_delivered = delivered.decision.velocity
                last_delivered_monotonic = issuance_monotonic
                active_setpoint_deadline = candidate_setpoint_deadline
                if not entered_offboard:
                    try:
                        await self._await_before_deadline(
                            self._adapter.start(),
                            _earlier(deadline, active_setpoint_deadline),
                        )
                    except Exception as error:  # adapter errors are implementation-specific
                        await fail(
                            f"PX4 refused the shielded Offboard route: {error}",
                            RouteTerminalReason.OFFBOARD_REFUSED,
                        )
                    entered_offboard = True
                ticks += 1
                if nominal.reached and decision.velocity.speed_m_s == 0.0:
                    return RouteRunResult(
                        reached=True,
                        ticks=ticks,
                        shield_interventions=interventions,
                        final_verdict=final_verdict,
                        session_record=self._session.record.as_document(),
                    )
                remaining = started_at + ticks / self._stream_hz - self._monotonic()
                if remaining > 0:
                    await self._await_before_deadline(
                        self._sleep(remaining),
                        _earlier(deadline, active_setpoint_deadline),
                    )
            await fail(
                f"Route timed out after {timeout_s:g} s.",
                RouteTerminalReason.TIMEOUT,
            )
        except asyncio.CancelledError:
            assert self._profile.offboard is not None
            try:
                await execute_fallback(self._profile.offboard.fallback_sequence)
            except Exception:
                # Cancellation must still propagate. The fallback task is
                # shielded and therefore continues even if this waiter is
                # cancelled again.
                pass
            raise
        except OffboardRouteExecutionError:
            raise
        except TimeoutError as error:
            if self._monotonic() >= deadline:
                await fail(
                    f"Route timed out after {timeout_s:g} s.",
                    RouteTerminalReason.TIMEOUT,
                )
            await fail(
                f"Shielded route execution missed an active deadline: {error}",
                RouteTerminalReason.INTERNAL_FAILURE,
            )
        except Exception as error:
            await fail(
                f"Shielded route execution failed closed: {error}",
                RouteTerminalReason.INTERNAL_FAILURE,
            )
        finally:
            self._session.stop()
            try:
                await self._adapter.stop()
            except Exception:
                pass
        raise AssertionError("The route loop must return or raise.")

    def _require_fresh_state(self, state: RouteState, now: datetime) -> None:
        if state.observed_at.tzinfo is None or state.observed_at.utcoffset() is None:
            raise ValueError("Route telemetry timestamp must be timezone-aware.")
        age_s = (now - state.observed_at.astimezone(UTC)).total_seconds()
        if age_s < 0 or age_s > self._telemetry_max_age_s:
            raise ValueError(
                f"Route telemetry is {age_s:.2f} s old; limit is "
                f"{self._telemetry_max_age_s:g} s."
            )

    def _utc_now(self) -> datetime:
        moment = self._now()
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("The route clock must be timezone-aware.")
        return moment.astimezone(UTC)

    async def _await_before_deadline(self, awaitable, deadline: float):
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            if hasattr(awaitable, "close"):
                awaitable.close()
            raise TimeoutError("The route deadline elapsed before an awaited operation.")
        result = await asyncio.wait_for(awaitable, timeout=remaining)
        if self._monotonic() > deadline:
            raise TimeoutError(
                "An awaited operation outlived the active setpoint deadline."
            )
        return result

    def _setpoint_document(
        self, stream_id: str, sequence: int, now: datetime, velocity: Velocity
    ) -> dict[str, object]:
        assert self._profile.offboard is not None
        return {
            "contract_version": "v0.1",
            "vehicle_id": self._profile.vehicle_id,
            "stream_id": stream_id,
            "sequence": sequence,
            "issued_at": now.isoformat().replace("+00:00", "Z"),
            "ttl_s": self._profile.offboard.max_setpoint_ttl_s,
            "frame": self._profile.offboard.frame,
            "kind": "velocity",
            "validity": "valid",
            "velocity": {
                "x_m_s": velocity.x_m_s,
                "y_m_s": velocity.y_m_s,
                "z_m_s": velocity.z_m_s,
                "yaw_rate_deg_s": velocity.yaw_rate_deg_s,
            },
        }


__all__ = [
    "OffboardRouteExecutionError",
    "OffboardRouteExecutor",
    "RouteRunResult",
    "RouteState",
    "RouteTick",
]


def _slew_velocity(previous: Velocity, target: Velocity, max_delta_m_s: float) -> Velocity:
    """Move toward a nominal translation without violating the next boundary tick."""
    dx = target.x_m_s - previous.x_m_s
    dy = target.y_m_s - previous.y_m_s
    dz = target.z_m_s - previous.z_m_s
    delta = hypot(dx, dy, dz)
    if delta <= max_delta_m_s or target.speed_m_s == 0.0:
        # Exact zero is the boundary's deliberately immediate shield revocation.
        return target
    scale = max_delta_m_s / delta
    return Velocity(
        previous.x_m_s + dx * scale,
        previous.y_m_s + dy * scale,
        previous.z_m_s + dz * scale,
        target.yaw_rate_deg_s,
    )


def _earlier(route_deadline: float, setpoint_deadline: float | None) -> float:
    return (
        route_deadline
        if setpoint_deadline is None
        else min(route_deadline, setpoint_deadline)
    )
