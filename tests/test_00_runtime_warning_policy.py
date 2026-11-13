"""Activate warnings-as-errors for RuntimeWarning for the unittest process."""

from __future__ import annotations

import unittest

import tests.warning_policy  # noqa: F401


class RuntimeWarningPolicyTests(unittest.TestCase):
    def test_runtime_warning_policy_module_loaded(self) -> None:
        import warnings

        matching = [item for item in warnings.filters if item[0] == "error" and item[2] is RuntimeWarning]
        self.assertTrue(matching, "expected RuntimeWarning to be configured as error")


if __name__ == "__main__":
    unittest.main()
