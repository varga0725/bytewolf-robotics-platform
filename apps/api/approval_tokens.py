"""Short-lived, single-use approval tokens bound to operator and plan hash."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import os
import secrets
import threading


class ApprovalTokenError(RuntimeError):
    """Raised when an approval token cannot be redeemed."""


_DEFAULT_TTL_S = 300


def default_token_ttl_seconds(environment: os._Environ[str] | None = None) -> int:
    values = os.environ if environment is None else environment
    raw = values.get("BYTEWOLF_APPROVAL_TOKEN_TTL_SECONDS", str(_DEFAULT_TTL_S)).strip()
    try:
        ttl = int(raw)
    except ValueError:
        return _DEFAULT_TTL_S
    if ttl < 30 or ttl > 3600:
        return _DEFAULT_TTL_S
    return ttl


@dataclass
class _TokenRecord:
    operator: str
    plan_hash: str
    plan_id: str
    session_id: str
    expires_at: datetime
    used: bool = False


class ApprovalTokenStore:
    """In-memory approval tokens — never logged or persisted."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._records: dict[str, _TokenRecord] = {}

    def issue(
        self,
        *,
        operator: str,
        session_id: str,
        plan_id: str,
        plan_hash: str,
        ttl_seconds: int | None = None,
    ) -> str:
        if not plan_hash or not plan_id.strip():
            raise ApprovalTokenError("A plan hash is required before issuing an approval token.")
        ttl = default_token_ttl_seconds() if ttl_seconds is None else ttl_seconds
        token = secrets.token_urlsafe(32)
        record = _TokenRecord(
            operator=operator,
            plan_hash=plan_hash,
            plan_id=plan_id,
            session_id=session_id,
            expires_at=datetime.now(UTC) + timedelta(seconds=ttl),
        )
        with self._lock:
            self._purge_expired_locked()
            self._records[token] = record
        return token

    def validate(
        self,
        token: str,
        *,
        operator: str,
        session_id: str,
        plan_id: str,
        plan_hash: str,
    ) -> str:
        """Check a token without consuming it; returns the bound operator."""
        if not token.strip():
            raise ApprovalTokenError("Approval token is required.")
        with self._lock:
            self._purge_expired_locked()
            record = self._records.get(token)
            if record is None:
                raise ApprovalTokenError("Approval token is unknown or expired.")
            if record.used:
                raise ApprovalTokenError("Approval token was already used.")
            if datetime.now(UTC) >= record.expires_at:
                del self._records[token]
                raise ApprovalTokenError("Approval token expired.")
            if record.operator != operator:
                raise ApprovalTokenError("Approval token is not valid for this operator.")
            if record.session_id != session_id:
                raise ApprovalTokenError("Approval token is not valid for this session.")
            if record.plan_id != plan_id:
                raise ApprovalTokenError("Approval token is not valid for this plan.")
            if record.plan_hash != plan_hash:
                raise ApprovalTokenError("Approval token does not match the plan hash.")
            return record.operator

    def consume(self, token: str) -> None:
        with self._lock:
            record = self._records.pop(token, None)
            if record is None:
                raise ApprovalTokenError("Approval token is unknown or expired.")

    def invalidate_for_session(self, session_id: str) -> None:
        with self._lock:
            self._purge_expired_locked()
            doomed = [
                key
                for key, item in self._records.items()
                if item.session_id == session_id and not item.used
            ]
            for key in doomed:
                del self._records[key]

    def _purge_expired_locked(self) -> None:
        now = datetime.now(UTC)
        expired = [key for key, item in self._records.items() if now >= item.expires_at]
        for key in expired:
            del self._records[key]