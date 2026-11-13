"""Launcher stderr temp logs must not leak across P0 runs."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from simulation.scenarios.scenarios import P0_SCENARIOS, ScenarioRunner
from simulation.scenarios.sitl_startup import unlink_stderr_log


class SitlStderrLogCleanupTests(unittest.TestCase):
    def test_unlink_stderr_log_removes_the_file(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as handle:
            path = Path(handle.name)
        path.write_text("launcher output\n", encoding="utf-8")
        process = Mock()
        process.stderr_log_path = path

        unlink_stderr_log(process)

        self.assertFalse(path.exists())

    def test_stop_sitl_unlinks_the_launcher_log(self) -> None:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".log") as handle:
            path = Path(handle.name)
        path.write_text("output\n", encoding="utf-8")
        process = Mock(pid=4242)
        process.wait = Mock()
        process.stderr_log_path = path

        ScenarioRunner(terminate_process=Mock())._stop_sitl(process)

        self.assertFalse(path.exists())

    def test_runner_removes_log_after_a_normal_sitl_lifecycle(self) -> None:
        held_paths: list[Path] = []

        def starter(*_args, **kwargs) -> Mock:
            process = Mock(pid=90210)
            process.poll.return_value = None
            process.wait = Mock(return_value=0)
            log_handle = kwargs.get("stderr")
            assert log_handle is not None
            held_paths.append(Path(log_handle.name))
            return process

        runner = ScenarioRunner(
            command_runner=Mock(return_value=Mock(returncode=0, stdout="", stderr="")),
            sitl_command=("sitl",),
            process_starter=starter,
            readiness_check=Mock(return_value=True),
            terminate_process=Mock(),
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            runner.run(P0_SCENARIOS[:1], Path(temporary_directory))

        self.assertEqual(len(held_paths), 1)
        self.assertFalse(held_paths[0].exists())

    def test_runner_removes_log_after_a_launch_failure(self) -> None:
        held_paths: list[Path] = []

        def starter(*_args, **kwargs) -> Mock:
            process = Mock(pid=90210)
            process.poll.return_value = 2
            process.wait = Mock(return_value=2)
            log_handle = kwargs.get("stderr")
            assert log_handle is not None
            path = Path(log_handle.name)
            log_handle.write("qt@5 missing\n")
            log_handle.flush()
            held_paths.append(path)
            return process

        runner = ScenarioRunner(
            command_runner=Mock(),
            sitl_command=("sitl",),
            process_starter=starter,
            readiness_check=Mock(return_value=True),
            terminate_process=Mock(),
            sleep=Mock(),
        )

        with TemporaryDirectory() as temporary_directory:
            report = json.loads(runner.run(P0_SCENARIOS[:1], Path(temporary_directory)).read_text())

        self.assertEqual(report["results"][0]["status"], "blocked")
        self.assertEqual(len(held_paths), 1)
        self.assertFalse(held_paths[0].exists())


if __name__ == "__main__":
    unittest.main()
