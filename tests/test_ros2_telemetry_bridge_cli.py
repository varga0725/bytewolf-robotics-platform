"""Argument coverage for the optional ROS 2 telemetry bridge entry point."""

from pathlib import Path
import subprocess
import unittest

from brain.cli.ros2_telemetry_bridge import parse_arguments


class Ros2TelemetryBridgeCliTests(unittest.TestCase):
    def test_accepts_a_bounded_px4_discovery_timeout(self) -> None:
        arguments = parse_arguments(("--connection-timeout", "7.5"))

        self.assertEqual(arguments.connection_timeout, 7.5)

    def test_entry_point_imports_in_the_deployed_python_310_environment(self) -> None:
        root = Path(__file__).resolve().parents[1]
        interpreter = root / ".venv-ros" / "bin" / "python"
        if not interpreter.is_file():
            self.skipTest("The optional ROS 2 Python 3.10 environment is not installed.")

        completed = subprocess.run(
            (str(interpreter), "-m", "brain.cli.ros2_telemetry_bridge", "--help"),
            cwd=root,
            capture_output=True,
            text=True,
            timeout=60.0,
            check=False,
        )

        self.assertEqual(completed.returncode, 0, completed.stderr)


if __name__ == "__main__":
    unittest.main()
