"""Static guarantees that the pymavlink telemetry backend never writes or commands PX4."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


class PymavlinkTelemetryReadOnlyStaticTests(unittest.TestCase):
    _FORBIDDEN_SUBSTRINGS = (
        "param_set",
        "param_set_send",
        "PARAM_SET",
        "mission_upload",
        "mission_",
        ".arm(",
        "takeoff(",
        "disarm",
        "set_mode",
        "manual_control_send",
        "command_long_send",
        "command_long",
        "command_int",
        "mav_cmd",
        "arducopter_arm",
        "param_request_list_send",
        "wait_heartbeat(",
    )

    _WRITE_SEND_SUFFIX = re.compile(r"\w+_send\b")

    def test_module_sources_exclude_write_and_command_apis(self) -> None:
        root = Path(__file__).resolve().parents[1] / "brain" / "telemetry"
        paths = (
            root / "pymavlink_relay.py",
            root / "loopback_endpoint.py",
        )
        for path in paths:
            text = path.read_text(encoding="utf-8").lower()
            for needle in self._FORBIDDEN_SUBSTRINGS:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle.lower(), text, f"{needle} found in {path}")
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for match in self._WRITE_SEND_SUFFIX.findall(text):
                with self.subTest(path=path.name, send=match):
                    self.assertNotIn(
                        match,
                        text,
                        f"unexpected *_send call pattern {match!r} in {path}",
                    )
            send_calls = [
                node
                for node in ast.walk(tree)
                if isinstance(node, ast.Call)
                and (
                    (isinstance(node.func, ast.Attribute) and node.func.attr.endswith("_send"))
                    or (isinstance(node.func, ast.Attribute) and node.func.attr == "send")
                )
            ]
            self.assertEqual(send_calls, [], f"mavlink send call in {path}")


if __name__ == "__main__":
    unittest.main()