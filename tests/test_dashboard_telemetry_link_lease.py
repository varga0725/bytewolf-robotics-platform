"""Link-lease holder PID restrictions on the dashboard bridge."""

from __future__ import annotations

import argparse
import os
import unittest
from unittest import mock

from brain.cli import dashboard_telemetry


class AllowedLeaseHolderPidTests(unittest.TestCase):
    def test_rejects_unrelated_pid(self) -> None:
        args = argparse.Namespace(allow_link_lease_holder_pid=[1])
        with mock.patch.object(os, "getppid", return_value=424242):
            with self.assertRaises(SystemExit) as ctx:
                dashboard_telemetry._allowed_lease_holders(args)
            self.assertEqual(ctx.exception.code, 2)

    def test_accepts_parent_pid(self) -> None:
        args = argparse.Namespace(allow_link_lease_holder_pid=[424242])
        with mock.patch.object(os, "getppid", return_value=424242):
            holders = dashboard_telemetry._allowed_lease_holders(args)
        self.assertEqual(holders, frozenset({424242}))


if __name__ == "__main__":
    unittest.main()