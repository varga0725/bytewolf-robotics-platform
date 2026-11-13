"""Asyncio test base (guard runs on every test via :func:`TestCase.run` wrapping)."""

from __future__ import annotations

import unittest

from tests import unawaited_coroutine_guard


class GuardedIsolatedAsyncioTestCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        unawaited_coroutine_guard.clear()
        super().setUp()
