"""Append-only JSONL audit for command propose / approve / deny / refusal events."""

from __future__ import annotations

from datetime import UTC, datetime
import json
import os
from pathlib import Path
import re
import stat
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
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            os.chmod(self._path.parent, stat.S_IRWXU)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            fd = os.open(self._path, flags, stat.S_IRUSR | stat.S_IWUSR)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(line)
                    handle.flush()
            except Exception:
                os.close(fd)
                raise


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


_TOKEN_VALUE_RE = re.compile(r"[A-Za-z0-9_\-]{20,}")


def _redact_token_like_substrings(text: str) -> str:
    return _TOKEN_VALUE_RE.sub("[redacted-token]", text)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        if _IPV4_RE.search(value):
            raise ValueError("Command audit must not record IP addresses.")
        lowered = value.lower()
        if "approval token" in lowered or "approval_token" in lowered:
            return _redact_token_like_substrings(value)
        if any(forbidden in lowered for forbidden in _FORBIDDEN_SUBSTRINGS):
            if lowered.strip() in {"approval token", "token"}:
                return value
            return _redact_token_like_substrings(value)
        return value
    if isinstance(value, dict):
        return {str(key): _sanitize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    return value


def _reject_forbidden_audit_line(line: str) -> None:
    try:
        body = json.loads(line.strip())
    except json.JSONDecodeError:
        body = None
    if isinstance(body, dict):
        if "approval_token" in body:
            raise ValueError("Command audit must never record approval token values.")
        for value in _walk_string_values(body.get("gate_results")):
            if _looks_like_approval_secret(value):
                raise ValueError("Command audit must never record approval token values.")
    elif re.search(r'"approval_token"\s*:\s*"[^"]+"', line):
        raise ValueError("Command audit must never record approval token values.")
    if _IPV4_RE.search(line):
        match = _IPV4_RE.search(line)
        if match and match.group(0) != "127.0.0.1":
            raise ValueError("Command audit must not record host IP addresses.")


def _walk_string_values(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, dict):
        collected: list[str] = []
        for item in node.values():
            collected.extend(_walk_string_values(item))
        return collected
    if isinstance(node, list):
        collected: list[str] = []
        for item in node:
            collected.extend(_walk_string_values(item))
        return collected
    return []


def _looks_like_approval_secret(text: str) -> bool:
    lowered = text.lower()
    if "approval token" in lowered or lowered.startswith("bwat_"):
        return _TOKEN_VALUE_RE.search(text) is not None
    return False


def plan_sha256(plan_path: Path) -> str:
    from hashlib import sha256

    if not plan_path.is_file():
        raise FileNotFoundError(f"Reviewed plan file is missing: '{plan_path}'.")
    return sha256(plan_path.read_bytes()).hexdigest()