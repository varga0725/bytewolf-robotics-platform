"""Asyncio test base that fails on unawaited coroutines after each test."""

from __future__ import annotations

import unittest

from tests import unawaited_coroutine_guard


class GuardedIsolatedAsyncioTestCase(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        unawaited_coroutine_guard.clear()
        try:
            super().tearDown()
        finally:
            unawaited_coroutine_guard.check_after_gc()
