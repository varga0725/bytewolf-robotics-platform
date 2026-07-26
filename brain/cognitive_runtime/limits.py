"""Enforce a ToolPolicy's rate and concurrency limits at call time.

The Plugin SDK's ToolPolicy declares ``rate_per_min`` and ``max_concurrent``;
this is where the runtime actually honours them. A ``LimitEnforcer`` is held per
capability across turns, so the window and the in-flight count persist rather
than resetting every message. Timeout is enforced separately, in the turn loop.

A rejected call is refused before the capability runs and surfaces as a
``denied`` trace entry with a reason -- never a silent drop.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
import threading
import time
from typing import Any


@dataclass
class LimitEnforcer:
    """A per-capability rate window and concurrency counter, thread-safe."""

    rate_per_min: int | None = None
    max_concurrent: int | None = None
    clock: Callable[[], float] = time.monotonic
    _recent: deque[float] = field(default_factory=deque, init=False)
    _active: int = field(default=0, init=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def acquire(self) -> str | None:
        """Reserve a slot, or return a denial reason without reserving one."""
        with self._lock:
            now = self.clock()
            if self.rate_per_min is not None:
                cutoff = now - 60.0
                while self._recent and self._recent[0] < cutoff:
                    self._recent.popleft()
                if len(self._recent) >= self.rate_per_min:
                    return f"rate limit {self.rate_per_min}/min exceeded"
            if self.max_concurrent is not None and self._active >= self.max_concurrent:
                return f"concurrency limit {self.max_concurrent} exceeded"
            if self.rate_per_min is not None:
                self._recent.append(now)
            self._active += 1
            return None

    def release(self) -> None:
        """Free a slot reserved by a successful ``acquire``."""
        with self._lock:
            if self._active > 0:
                self._active -= 1


def limits_of(policy_limits: dict[str, Any] | None) -> tuple[int | None, int | None]:
    """Read (rate_per_min, max_concurrent) from a ToolPolicy limits object."""
    limits = policy_limits or {}
    rate = limits.get("rate_per_min")
    concurrent = limits.get("max_concurrent")
    return (
        int(rate) if rate is not None else None,
        int(concurrent) if concurrent is not None else None,
    )
