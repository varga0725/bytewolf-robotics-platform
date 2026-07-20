"""Narrow Python boundary for the local Pi agent runner.

Pi is the conversational harness only.  It cannot reach MAVSDK or PX4: it may
ask the dashboard gateway to review a natural-language flight request, which
remains subject to the existing deterministic SafetyGate and explicit approval.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any


_MAX_REPLY_CHARS = 2_000
# The runner's post-turn hook reports one of these words and nothing else.
# Anything unrecognised is treated as a failed hook rather than rendered.
_MEMORY_UPDATE_STATES = frozenset({"updated", "skipped", "unavailable"})
# The briefing is bounded here as well as at the renderer: a runaway world log
# must not be able to push the conversation's own instructions out of context.
_MAX_WORLD_CONTEXT_CHARS = 1_200
_RUNNER_TIMEOUT_S = 60
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

    def __init__(self, *, runner: RunPi | None = None) -> None:
        self._runner = runner or _run_pi

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
        return PiAgentReply(reply.strip(), requested, _memory_update(response.get("memory_update")))


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
        raise PiAgentError("Pi agent timed out; the drone received no command.") from error
    if completed.returncode != 0:
        raise PiAgentError("Pi agent is unavailable; the drone received no command.")
    try:
        response = json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise PiAgentError("Pi agent returned invalid data; the drone received no command.") from error
    if not isinstance(response, dict):
        raise PiAgentError("Pi agent returned invalid data; the drone received no command.")
    return response


def _pi_environment(source: Mapping[str, str]) -> dict[str, str]:
    """Pass only model configuration and essential runtime discovery to Pi."""
    keys = ("PATH", "HOME", "NVIDIA_API_KEY", "NIM_MISSION_MODEL", "NIM_MEMORY_MODEL", "NIM_BASE_URL")
    return {key: source[key] for key in keys if key in source}
