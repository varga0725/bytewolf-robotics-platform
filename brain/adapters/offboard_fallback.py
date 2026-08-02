"""Carry out the fallback the watchdog decided. Something has to.

The watchdog returns an ordered sequence; until this existed, nothing performed
it, and a SITL run showed exactly how much that mattered.

MAVSDK re-sends the last Offboard setpoint at 20 Hz of its own accord -- its own
documentation says so, and PX4 requires at least 2 Hz to stay in the mode. So a
producer going silent does **not** make the link go silent. PX4 keeps seeing a
healthy stream and keeps flying the last thing it was told, indefinitely. In the
run that found this, the vehicle sat in Offboard through twenty seconds of
silence, still creeping forward on a command whose author had stopped talking.

That is the coasting failure the whole boundary exists to prevent, and the
watchdog detecting it changes nothing on its own. Detection and action are
different things, and this is the action.

It lives outside ``brain/control`` for the usual reason: it reaches ``action``
and the Offboard mode switch, and the package that decides whether a setpoint
may fly must not be able to reach either.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from brain.control.contract import Velocity


#: Every step the twin is allowed to ask for, and what each one does.
#: A step this executor does not know is refused rather than skipped: silently
#: ignoring a fallback step would leave a vehicle in a state nobody chose.
KNOWN_STEPS = frozenset({"zero_velocity", "hold", "land", "rtl"})

_ZERO = Velocity(0.0, 0.0, 0.0, 0.0)


class FallbackExecutionError(RuntimeError):
    """Raised when a fallback step could not be carried out at all."""


@dataclass
class FallbackRecord:
    """What the fallback actually managed to do, step by step."""

    completed: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)

    @property
    def stopped_the_vehicle(self) -> bool:
        """Whether the motion-stopping step succeeded, which is the point."""
        return "zero_velocity" in self.completed

    def as_document(self) -> dict[str, Any]:
        return {"completed": list(self.completed), "failed": dict(sorted(self.failed.items()))}


class OffboardFallbackExecutor:
    """Perform the twin's fallback sequence, in order, best effort per step.

    Best effort *per step*, not overall: a later step must still be attempted
    when an earlier one fails, because the sequence is ordered by how much it
    gives up. Stopping at the first failure would leave the vehicle holding a
    velocity because the command to leave Offboard happened to error.
    """

    def __init__(
        self, system: Any, adapter: Any, *, action_timeout_s: float = 5.0
    ) -> None:
        if action_timeout_s <= 0:
            raise ValueError("action_timeout_s must be positive.")
        self._system = system
        self._adapter = adapter
        self._action_timeout_s = action_timeout_s

    async def execute(self, sequence: Sequence[str]) -> FallbackRecord:
        """Run the sequence and report what each step did."""
        unknown = [step for step in sequence if step not in KNOWN_STEPS]
        if unknown:
            raise FallbackExecutionError(
                f"The twin asks for fallback steps this executor cannot perform: {unknown}."
            )
        record = FallbackRecord()
        for step in sequence:
            try:
                await asyncio.wait_for(
                    self._perform(step), timeout=self._action_timeout_s
                )
            except Exception as error:  # noqa: BLE001 - a failed step must not stop the rest
                record.failed[step] = str(error)
            else:
                record.completed.append(step)
        return record

    async def _perform(self, step: str) -> None:
        if step == "zero_velocity":
            # First, and first for a reason: it is the only step that stops the
            # vehicle without depending on a mode change being accepted.
            await self._adapter.send_velocity_async(_ZERO, self._adapter_frame())
            return
        if step == "hold":
            # Leaving Offboard is what actually ends MAVSDK's 20 Hz re-send.
            # Without it every later step competes with a stream still telling
            # PX4 what to do.
            await self._adapter.stop()
            await self._system.action.hold()
            return
        if step == "land":
            await self._system.action.land()
            return
        if step == "rtl":
            await self._system.action.return_to_launch()
            return
        raise FallbackExecutionError(f"Unknown fallback step '{step}'.")

    def _adapter_frame(self) -> str:
        from brain.adapters.offboard_velocity_adapter import SUPPORTED_FRAME

        return SUPPORTED_FRAME
