"""Activate global test hooks (import order: package guard, then warning policy)."""

from __future__ import annotations

import unittest

import tests  # noqa: F401  # installs unawaited-coroutine guard
import tests.warning_policy  # noqa: F401


class RuntimeWarningPolicyTests(unittest.TestCase):
    def test_runtime_warning_policy_module_loaded(self) -> None:
        import warnings

        from tests import unawaited_coroutine_guard

        matching = [item for item in warnings.filters if item[0] == "error" and item[2] is RuntimeWarning]
        self.assertTrue(matching, "expected RuntimeWarning to be configured as error")
        self.assertTrue(unawaited_coroutine_guard.violations() == ())


if __name__ == "__main__":
    unittest.main()
