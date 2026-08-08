"""MAVSDK child-process cleanup must not leak zombies from a long-running relay."""

from __future__ import annotations

import subprocess
import unittest
from unittest.mock import patch

from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server


class FakeProcess:
    def __init__(self, *, timeout_once: bool = False) -> None:
        self.timeout_once = timeout_once
        self.wait_calls: list[float] = []
        self.kill_calls = 0

    def wait(self, *, timeout: float) -> int:
        self.wait_calls.append(timeout)
        if self.timeout_once:
            self.timeout_once = False
            raise subprocess.TimeoutExpired("mavsdk_server", timeout)
        return 0

    def kill(self) -> None:
        self.kill_calls += 1


class FakeSystem:
    def __init__(self, process: FakeProcess) -> None:
        self._server_process = process
        self.stopped = False

    def _stop_mavsdk_server(self) -> None:
        self.stopped = True


class MavsdkLifecycleTests(unittest.TestCase):
    @patch("brain.cli.mavsdk_lifecycle.release_link_if_mine")
    def test_reaps_the_child_after_the_library_stops_it(self, release) -> None:
        process = FakeProcess()
        system = FakeSystem(process)

        stop_owned_mavsdk_server(system)

        self.assertTrue(system.stopped)
        self.assertEqual(process.wait_calls, [5.0])
        self.assertEqual(process.kill_calls, 0)
        release.assert_called_once_with()

    @patch("brain.cli.mavsdk_lifecycle.release_link_if_mine")
    def test_forces_and_reaps_a_child_that_does_not_exit_in_time(self, release) -> None:
        process = FakeProcess(timeout_once=True)

        stop_owned_mavsdk_server(FakeSystem(process))

        self.assertEqual(process.wait_calls, [5.0, 5.0])
        self.assertEqual(process.kill_calls, 1)
        release.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
