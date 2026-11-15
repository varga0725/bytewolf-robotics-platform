"""Default live telemetry snapshot path must stay out of version control."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import unittest
import unittest.mock

from brain.telemetry.dashboard_snapshot_path import default_dashboard_snapshot_path


class DashboardSnapshotPathTests(unittest.TestCase):
    def test_default_path_is_under_gitignored_var_dashboard(self) -> None:
        path = default_dashboard_snapshot_path()
        self.assertEqual(path, Path("var/dashboard/live-telemetry.json"))
        self.assertFalse(path.is_absolute())

    def test_default_path_is_not_git_tracked(self) -> None:
        root = Path(__file__).resolve().parents[1]
        path = default_dashboard_snapshot_path()
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", str(path)],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(tracked.returncode, 0, "default snapshot path must not be a tracked file")

    def test_env_override(self) -> None:
        custom = Path("/tmp/bytewolf-custom-telemetry.json")
        with unittest.mock.patch.dict(os.environ, {"BYTEWOLF_DASHBOARD_TELEMETRY_FILE": str(custom)}):
            self.assertEqual(default_dashboard_snapshot_path(), custom)

if __name__ == "__main__":
    unittest.main()