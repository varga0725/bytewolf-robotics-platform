"""Narrow Python boundary for the local Pi agent runner.

Pi is the conversational harness only.  It cannot reach MAVSDK or PX4: it may
ask the dashboard gateway to review a natural-language flight request, which
remains subject to the existing deterministic SafetyGate and explicit approval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any
import uuid


_LOGGER = logging.getLogger(__name__)

_MAX_REPLY_CHARS = 2_000
# The runner's post-turn hook reports one of these words and nothing else.
# Anything unrecognised is treated as a failed hook rather than rendered.
_MEMORY_UPDATE_STATES = frozenset({"updated", "skipped", "unavailable"})
# The briefing is bounded here as well as at the renderer: a runaway world log
# must not be able to push the conversation's own instructions out of context.
_MAX_WORLD_CONTEXT_CHARS = 1_200
_RUNNER_TIMEOUT_S = 60
# The runner authors its own stderr text precisely so it can be recorded without
# leaking request content or a key fragment. Bounded anyway: a runner that dies
# mid-stream must not be able to flood the operator's log.
_MAX_DIAGNOSTIC_CHARS = 2_000
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_RUNNER_PATH = _PROJECT_ROOT / "apps" / "pi_agent" / "runner.mjs"


class PiAgentError(RuntimeError):
    """The local conversational harness is unavailable or returned invalid data."""


@dataclass(frozen=True)
class PiAgentReply:
    text: str
    requests_drone_action: bool
    memory_update: str = "unavailable"


RunPi = Callable[[dict[str, object]], Mapping[str, object]]


class PiAgentClient:
    """Invoke Pi with a browser-bound persistent session.

    The runner is deliberately a subprocess rather than a browser-facing Node
    server.  The FastAPI process owns the local API boundary; Pi receives just
    the session ID and text and emits one typed reply on stdout.
    """

    def __init__(self, *, runner: RunPi | None = None, memory_hook: Any = None) -> None:
        self._runner = runner or _run_pi
        # When set, the runner's raw `memory_delta` is validated, admitted and
        # stored by the cognitive-hooks runtime; without it, a legacy runner's
        # `memory_update` status word is used as-is.
        self._memory_hook = memory_hook

    def converse(
        self,
        session_id: str,
        text: str,
        world_context: str = "",
        capability_context: str = "",
    ) -> PiAgentReply:
        """Run one turn with read-only briefings of the world and of itself.

        Both are resolved in Python and passed in as text: Pi never gains a way
        to read the store or the safety profile itself, sees nothing the
        dashboard would not have shown a human, and holds no second copy of a
        limit that `twin.yaml` already decides.
        """
        if not session_id.strip() or not text.strip():
            raise PiAgentError("Pi agent request is invalid.")
        request: dict[str, object] = {"session_id": session_id, "text": text}
        if world_context.strip():
            request["world_context"] = world_context[:_MAX_WORLD_CONTEXT_CHARS]
        if capability_context.strip():
            request["capability_context"] = capability_context[:_MAX_WORLD_CONTEXT_CHARS]
        try:
            response = self._runner(request)
        except PiAgentError:
            raise
        except Exception as error:
            raise PiAgentError("Pi agent is unavailable; the drone received no command.") from error
        reply = response.get("text")
        requested = response.get("requests_drone_action")
        if (
            not isinstance(reply, str)
            or not reply.strip()
            or len(reply) > _MAX_REPLY_CHARS
            or not isinstance(requested, bool)
        ):
            raise PiAgentError("Pi agent returned an invalid reply; the drone received no command.")
        return PiAgentReply(reply.strip(), requested, self._resolve_memory(session_id, response))

    def _resolve_memory(self, session_id: str, response: Mapping[str, object]) -> str:
        """Derive the memory status, routing a raw delta through the new runtime."""
        if self._memory_hook is not None and "memory_delta" in response:
            turn_id = f"{session_id}:{uuid.uuid4().hex}"
            status = self._memory_hook.record(session_id, turn_id, response.get("memory_delta"))
            return status if isinstance(status, str) and status in _MEMORY_UPDATE_STATES else "unavailable"
        return _memory_update(response.get("memory_update"))


def _memory_update(value: object) -> str:
    """Never let the hook's channel carry anything but a status word.

    A broken or hostile runner must not be able to smuggle remembered text —
    or an error message — into the dashboard through this field.
    """
    return value if isinstance(value, str) and value in _MEMORY_UPDATE_STATES else "unavailable"


def _run_pi(request: dict[str, object]) -> Mapping[str, object]:
    node = shutil.which("node")
    if node is None or not _RUNNER_PATH.is_file():
        raise PiAgentError("Pi agent runtime is not installed; the drone received no command.")
    environment = _pi_environment(os.environ)
    try:
        completed = subprocess.run(
            [node, str(_RUNNER_PATH)],
            input=json.dumps(request),
            capture_output=True,
            text=True,
            cwd=_PROJECT_ROOT,
            env=environment,
            timeout=_RUNNER_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        _record_diagnostic("timed out", error.stderr)
        raise PiAgentError("Pi agent timed out; the drone received no command.") from error
    if completed.returncode != 0:
        _record_diagnostic(f"exited with status {completed.returncode}", completed.stderr)
        raise PiAgentError("Pi agent is unavailable; the drone received no command.")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        _record_diagnostic("returned data that is not JSON", completed.stderr)
        raise PiAgentError("Pi agent returned invalid data; the drone received no command.") from error
    if not isinstance(response, dict):
        _record_diagnostic("returned JSON that is not an object", completed.stderr)
        raise PiAgentError("Pi agent returned invalid data; the drone received no command.")
    return response


def _record_diagnostic(what_happened: str, stderr: str | bytes | None) -> None:
    """Log why the runner failed, without changing what the operator is told.

    The runner writes a deliberately safe one-line cause to stderr; until now
    nothing read it, so every failure reached the dashboard as the same fixed
    sentence with the reason discarded. The reply text is unchanged — this only
    stops the cause from being thrown away.
    """
    if isinstance(stderr, bytes):
        stderr = stderr.decode("utf-8", errors="replace")
    detail = (stderr or "").strip()[:_MAX_DIAGNOSTIC_CHARS] or "no diagnostic output"
    _LOGGER.warning("Pi agent runner %s: %s", what_happened, detail)


def _pi_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Pass only model configuration and essential runtime discovery to Pi."""
    keys = ("PATH", "HOME", "NVIDIA_API_KEY", "NIM_MISSION_MODEL", "NIM_MEMORY_MODEL", "NIM_BASE_URL")
    return {key: source[key] for key in keys if key in source}
