"""Carry an approved velocity setpoint to PX4, and nothing else.

This is the only implementation of ``brain.control.session.VelocityAdapter``,
and it lives here rather than in ``brain/control`` for a reason worth stating:
``brain/control`` is MAVSDK-free by static test, so the boundary that decides
whether a setpoint may fly cannot reach the API that flies it. The decision and
the delivery are separable, and keeping them in different packages is what makes
that checkable rather than merely intended.

Only ``body_frd`` is served. MAVSDK's ``set_velocity_body`` takes a yaw *rate*,
which is exactly what the setpoint contract carries; ``set_velocity_ned`` takes a
yaw *angle*, which it does not. Serving ``local_ned`` would mean reinterpreting
one as the other here, silently, in a movement command -- so it is refused
instead. The twin declares ``body_frd`` for the same reason plus a better one:
it is the frame the obstacle contract uses, so a shield reading obstacles and
writing velocities needs no conversion between them.

MAVSDK's Offboard plugin also exposes ``set_actuator_control``, ``set_attitude``
and position setpoints. None of them are reachable through this class. The
method below is the whole surface, and the boundary has already approved what
passes through it.
"""

from __future__ import annotations

from typing import Any

from brain.control.contract import Velocity
from brain.control.session import OffboardAdapterError


#: The one frame this adapter can carry without reinterpreting a unit.
SUPPORTED_FRAME = "body_frd"


class MavsdkVelocityAdapter:
    """Deliver approved body-frame velocities through MAVSDK's Offboard plugin.

    The MAVSDK system is injected rather than constructed, so every test here
    runs without a flight stack, and so this class never decides *which* vehicle
    it is talking to.
    """

    def __init__(self, system: Any, *, offboard_module: Any = None) -> None:
        self._system = system
        # Imported lazily by default: MAVSDK is a heavyweight dependency and
        # this module is imported by tests that never fly.
        self._offboard_module = offboard_module
        self._started = False

    @property
    def started(self) -> bool:
        """Whether PX4 has been asked to enter Offboard mode."""
        return self._started

    async def start(self) -> None:
        """Enter Offboard mode, having already sent a setpoint.

        PX4 refuses to enter Offboard without a setpoint stream already running,
        and rightly so: the mode means "follow what you are being told", and
        entering it with nothing to follow is undefined. The caller must send at
        least one velocity before this.
        """
        if self._started:
            return
        try:
            await self._system.offboard.start()
        except Exception as error:  # MAVSDK raises its own OffboardError
            raise OffboardAdapterError(f"PX4 refused to enter Offboard mode: {error}") from error
        self._started = True

    async def stop(self) -> None:
        """Leave Offboard mode, letting PX4's own mode logic take over.

        Never raises. This runs on the way out of a flight, including a failing
        one, and a teardown that throws would replace the original failure with
        its own.
        """
        if not self._started:
            return
        self._started = False
        try:
            await self._system.offboard.stop()
        except Exception:  # noqa: BLE001 - teardown must not mask the real fault
            return

    async def send_velocity_async(self, velocity: Velocity, frame: str) -> None:
        """Deliver one approved velocity, or raise ``OffboardAdapterError``."""
        if frame != SUPPORTED_FRAME:
            raise OffboardAdapterError(
                f"This adapter carries {SUPPORTED_FRAME} only; the setpoint declared {frame}. "
                "MAVSDK's NED velocity setpoint takes a yaw angle, not the yaw rate this "
                "contract carries, so serving it would mean reinterpreting one as the other."
            )
        body = self._velocity_body(velocity)
        try:
            await self._system.offboard.set_velocity_body(body)
        except Exception as error:  # MAVSDK raises its own OffboardError
            raise OffboardAdapterError(f"PX4 rejected the velocity setpoint: {error}") from error

    def _velocity_body(self, velocity: Velocity) -> Any:
        """Build MAVSDK's body-frame setpoint from the contract's own fields.

        The mapping is one-to-one and unit-for-unit: forward/right/down in m/s
        and a yaw rate in deg/s, which is what both sides already speak. There
        is deliberately no arithmetic here -- a conversion is a place a sign can
        be lost.
        """
        module = self._offboard_module
        if module is None:
            import mavsdk.offboard as module  # noqa: PLC0415 - deliberately lazy

            self._offboard_module = module
        return module.VelocityBodyYawspeed(
            velocity.x_m_s,       # forward
            velocity.y_m_s,       # right
            velocity.z_m_s,       # down, positive downward in both conventions
            velocity.yaw_rate_deg_s,
        )
