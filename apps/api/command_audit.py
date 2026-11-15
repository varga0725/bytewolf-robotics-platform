"""Append-only JSONL audit for command propose / approve / deny / refusal events."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import threading
from typing import Any

_FORBIDDEN_SUBSTRINGS = (
    "approval_token",
    "token",
    "secret",
    "api_key",
    "password",
    "tailscale",
)
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_DEFAULT_PATH = Path("var/command-audit/events.jsonl")


def default_audit_path(environment: os._Environ[str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    raw = values.get("BYTEWOLF_COMMAND_AUDIT_PATH", str(_DEFAULT_PATH)).strip()
    return Path(raw)


class CommandAuditLog:
    """Write operator command audit records without secrets or network identifiers."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = default_audit_path() if path is None else path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._path

    def append(
        self,
        *,
        event: str,
        operator: str,
        plan_hash: str | None,
        target_class: str,
        gate_results: dict[str, Any],
        approved_by: str | None = None,
    ) -> None:
        body: dict[str, Any] = {
            "event": event,
            "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "operator": operator,
            "plan_hash": plan_hash,
            "target_class": target_class,
            "gate_results": _sanitize_gate_results(gate_results),
        }
        if approved_by is not None:
            body["approved_by"] = approved_by
        line = json.dumps(body, sort_keys=True, separators=(",", ":")) + "\n"
        _reject_forbidden_audit_line(line)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line)
                handle.flush()


def _sanitize_gate_results(document: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in document.items():
        if not isinstance(key, str):
            continue
        lowered = key.lower()
        if any(forbidden in lowered for forbidden in _FORBIDDEN_SUBSTRINGS):
            continue
        cleaned[key] = _sanitize_value(value)
    return cleaned


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        if _IPV4_RE.search(value):
            raise ValueError("Command audit must not record IP addresses.")
        lowered = value.lower()
        if any(forbidden in lowered for forbidden in _FORBIDDEN_SUBSTRINGS):
            return "[redacted]"
        return value
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _reject_forbidden_audit_line(line: str) -> None:
    lowered = line.lower()
    if "approval_token" in lowered:
        raise ValueError("Command audit must never record approval tokens.")
    if _IPV4_RE.search(line):
        match = _IPV4_RE.search(line)
        if match and match.group(0) != "127.0.0.1":
            raise ValueError("Command audit must not record host IP addresses.")


def plan_sha256(plan_path: Path) -> str:
    from hashlib import sha256

    if not plan_path.is_file():
        raise FileNotFoundError(f"Reviewed plan file is missing: '{plan_path}'.")
    return sha256(plan_path.read_bytes()).hexdigest()