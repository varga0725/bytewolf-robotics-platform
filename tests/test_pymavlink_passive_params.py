"""Passive param reader must not emit commands other than PARAM_REQUEST_READ."""

from __future__ import annotations

import unittest
from pathlib import Path


class PymavlinkPassiveParamsReadOnlyTests(unittest.TestCase):
    _FORBIDDEN = (
        "param_set",
        "param_request_list_send",
        "command_long",
        "mission_",
        ".arm(",
    )

    def test_passive_params_module_allows_only_param_request_read_send(self) -> None:
        path = Path(__file__).resolve().parents[1] / "brain/telemetry/pymavlink_passive_params.py"
        text = path.read_text(encoding="utf-8").lower()
        for needle in self._FORBIDDEN:
            with self.subTest(needle=needle):
                self.assertNotIn(needle.lower(), text)
        self.assertIn("param_request_read_send", path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()