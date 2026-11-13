"""SITL launcher startup failure detection."""

from __future__ import annotations

import unittest
from io import StringIO
from unittest.mock import Mock

from simulation.scenarios.sitl_startup import (
    SITL_LAUNCH_FAILED,
    format_sitl_launch_failed,
    wait_for_sitl_launch,
)


class SitlStartupTests(unittest.TestCase):
    def test_reports_launch_failure_with_exit_code_and_stderr_tail(self) -> None:
        process = Mock()
        process.poll.side_effect = [None, 2]
        process.stderr = StringIO("line1\nmissing qt\n")

        message = wait_for_sitl_launch(process, startup_wait_s=1.0, sleep=lambda _s: None)

        self.assertIsNotNone(message)
        assert message is not None
        self.assertTrue(message.startswith(f"{SITL_LAUNCH_FAILED}: exit_code=2"))
        self.assertIn("missing qt", message)

    def test_returns_none_while_launcher_stays_alive_through_startup_window(self) -> None:
        process = Mock()
        process.poll.return_value = None

        message = wait_for_sitl_launch(process, startup_wait_s=0.5, sleep=lambda _s: None, poll_interval_s=0.1)

        self.assertIsNone(message)

    def test_format_includes_prefix(self) -> None:
        self.assertIn(SITL_LAUNCH_FAILED, format_sitl_launch_failed(1, "boom"))


if __name__ == "__main__":
    unittest.main()
