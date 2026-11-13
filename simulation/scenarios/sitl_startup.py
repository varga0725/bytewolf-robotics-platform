"""Detect headless SITL launcher failures before MAVLink connect timeouts."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Callable, Protocol

SITL_LAUNCH_FAILED = "sitl_launch_failed"
_STDERR_TAIL_LINES = 40


class LaunchedProcess(Protocol):
    """The poll/wait/stderr surface used while waiting for SITL to stay up."""

    def poll(self) -> int | None: ...

    @property
    def stderr(self) -> object | None: ...


def stderr_tail(process: LaunchedProcess, *, max_lines: int = _STDERR_TAIL_LINES) -> str:
    """Read the launcher stderr tail without blocking a still-running process."""
    log_path = getattr(process, "stderr_log_path", None)
    if isinstance(log_path, (str, Path)):
        path = Path(log_path)
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            lines = text.strip().splitlines()
            if len(lines) > max_lines:
                lines = lines[-max_lines:]
            return "\n".join(lines)

    stream = process.stderr
    if stream is None:
        return ""
    try:
        if hasattr(stream, "read"):
            payload = stream.read()
            if isinstance(payload, bytes):
                text = payload.decode("utf-8", errors="replace")
            else:
                text = str(payload)
        else:
            return ""
    except (OSError, ValueError):
        return ""
    lines = text.strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[-max_lines:]
    return "\n".join(lines)


def format_sitl_launch_failed(exit_code: int, tail: str) -> str:
    return f"{SITL_LAUNCH_FAILED}: exit_code={exit_code}\n{tail}".strip()


def wait_for_sitl_launch(
    process: LaunchedProcess,
    *,
    startup_wait_s: float,
    sleep: Callable[[float], None] = time.sleep,
    poll_interval_s: float = 0.25,
) -> str | None:
    """Return a ``sitl_launch_failed`` message when the launcher exits during startup."""
    waited = 0.0
    while waited < startup_wait_s:
        exit_code = process.poll()
        if isinstance(exit_code, int):
            return format_sitl_launch_failed(exit_code, stderr_tail(process))
        step = min(poll_interval_s, startup_wait_s - waited)
        sleep(step)
        waited += step
    exit_code = process.poll()
    if isinstance(exit_code, int):
        return format_sitl_launch_failed(exit_code, stderr_tail(process))
    return None
