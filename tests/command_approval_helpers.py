"""Shared helpers for command approval API tests."""

from __future__ import annotations

from pathlib import Path

from apps.api.command_audit import plan_sha256

SAMPLE_PLAN_ID = "b3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json"
IDENTITY_TEST_ENV = {"BYTEWOLF_OPERATOR_IDENTITY_TEST_MODE": "true"}


def seed_plan_file(plan_directory: Path, plan_id: str = SAMPLE_PLAN_ID, content: str = '{"mission_id":"demo"}') -> str:
    plan_directory.mkdir(parents=True, exist_ok=True)
    path = plan_directory / plan_id
    path.write_text(content, encoding="utf-8")
    return plan_sha256(path)


def issue_and_approve(client, session: str, plan_id: str) -> tuple[object, object]:
    token_response = client.post(
        "/api/v1/plans/approval-token",
        headers={"X-ByteWolf-Session": session},
        json={"plan_id": plan_id},
    )
    token_body = token_response.json()
    approved = client.post(
        "/api/v1/plans/approve",
        headers={"X-ByteWolf-Session": session},
        json={"plan_id": plan_id, "approval_token": token_body["approval_token"]},
    )
    return token_response, approved