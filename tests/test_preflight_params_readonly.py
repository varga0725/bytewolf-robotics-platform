"""Static guarantees that preflight parameter tooling never writes or commands PX4."""

from __future__ import annotations

import unittest
from pathlib import Path


class PreflightReadOnlyStaticTests(unittest.TestCase):
    _FORBIDDEN_SUBSTRINGS = (
        "param_set",
        "param_set_send",
        "PARAM_SET",
        "set_param",
        "mission_upload",
        "upload_mission",
        ".arm(",
        "takeoff(",
        "set_mode",
        "set_actuator",
        "offboard",
        "manual_control_send",
        "command_long_send",
    )

    def test_module_sources_exclude_write_and_command_apis(self) -> None:
        root = Path(__file__).resolve().parents[1] / "brain" / "preflight_params"
        sources = sorted(path for path in root.glob("**/*.py") if path.is_file())
        self.assertGreaterEqual(len(sources), 5)
        for path in sources:
            text = path.read_text(encoding="utf-8").lower()
            for needle in self._FORBIDDEN_SUBSTRINGS:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle.lower(), text, f"{needle} found in {path}")


if __name__ == "__main__":
    unittest.main()