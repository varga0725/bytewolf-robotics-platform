"""Test package: installing the unawaited-coroutine guard for any `tests.*` import."""

from __future__ import annotations

import os

# FastAPI TestClient peers identify as testclient/testserver; enable only under tests.
os.environ.setdefault("BYTEWOLF_OPERATOR_IDENTITY_TEST_MODE", "true")

from tests.unawaited_coroutine_guard import install as install_unawaited_coroutine_guard

install_unawaited_coroutine_guard()

__all__ = ["install_unawaited_coroutine_guard"]
