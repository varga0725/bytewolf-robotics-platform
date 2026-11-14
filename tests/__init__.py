"""Test package: installing the unawaited-coroutine guard for any `tests.*` import."""

from __future__ import annotations

from tests.unawaited_coroutine_guard import install as install_unawaited_coroutine_guard

install_unawaited_coroutine_guard()

__all__ = ["install_unawaited_coroutine_guard"]
