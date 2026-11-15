"""Server approval tokens are bound, expiring, and single-use."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import unittest
from unittest.mock import patch

from apps.api.approval_tokens import ApprovalTokenError, ApprovalTokenStore


class ApprovalTokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = ApprovalTokenStore()
        self.plan_hash = "abc123"

    def test_redeem_returns_bound_operator_as_approved_by(self) -> None:
        token = self.store.issue(
            operator="local",
            session_id="session-1",
            plan_id="plan-a",
            plan_hash=self.plan_hash,
            ttl_seconds=60,
        )
        approved_by = self.store.redeem(
            token, operator="local", session_id="session-1", plan_id="plan-a", plan_hash=self.plan_hash
        )
        self.assertEqual(approved_by, "local")

    def test_expired_token_is_refused(self) -> None:
        token = self.store.issue(
            operator="local",
            session_id="session-1",
            plan_id="plan-a",
            plan_hash=self.plan_hash,
            ttl_seconds=1,
        )
        record = self.store._records[token]
        stale = ApprovalTokenStore()
        stale._records[token] = type(record)(
            record.operator,
            record.plan_hash,
            record.plan_id,
            record.session_id,
            expires_at=datetime.now(UTC) - timedelta(seconds=1),
        )
        with self.assertRaisesRegex(ApprovalTokenError, "expired"):
            stale.redeem(
                token, operator="local", session_id="session-1", plan_id="plan-a", plan_hash=self.plan_hash
            )

    def test_reuse_is_refused(self) -> None:
        token = self.store.issue(
            operator="local",
            session_id="session-1",
            plan_id="plan-a",
            plan_hash=self.plan_hash,
            ttl_seconds=60,
        )
        self.store.redeem(
            token, operator="local", session_id="session-1", plan_id="plan-a", plan_hash=self.plan_hash
        )
        with self.assertRaisesRegex(ApprovalTokenError, "already used"):
            self.store.redeem(
                token, operator="local", session_id="session-1", plan_id="plan-a", plan_hash=self.plan_hash
            )

    def test_wrong_plan_hash_is_refused(self) -> None:
        token = self.store.issue(
            operator="local",
            session_id="session-1",
            plan_id="plan-a",
            plan_hash=self.plan_hash,
            ttl_seconds=60,
        )
        with self.assertRaisesRegex(ApprovalTokenError, "plan hash"):
            self.store.redeem(
                token, operator="local", session_id="session-1", plan_id="plan-a", plan_hash="deadbeef"
            )

    def test_wrong_operator_is_refused(self) -> None:
        token = self.store.issue(
            operator="local",
            session_id="session-1",
            plan_id="plan-a",
            plan_hash=self.plan_hash,
            ttl_seconds=60,
        )
        with self.assertRaisesRegex(ApprovalTokenError, "operator"):
            self.store.redeem(
                token, operator="operator-2", session_id="session-1", plan_id="plan-a", plan_hash=self.plan_hash
            )

    @patch.dict("os.environ", {"BYTEWOLF_APPROVAL_TOKEN_TTL_SECONDS": "5"}, clear=False)
    def test_ttl_env_clamps_out_of_range_values(self) -> None:
        from apps.api.approval_tokens import default_token_ttl_seconds

        self.assertEqual(default_token_ttl_seconds(), 300)


if __name__ == "__main__":
    unittest.main()