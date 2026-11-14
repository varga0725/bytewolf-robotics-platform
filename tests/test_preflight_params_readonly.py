"""Static guarantees that preflight parameter tooling never writes or commands PX4."""

from __future__ import annotations

import ast
import re
import unittest
from pathlib import Path


class PreflightReadOnlyStaticTests(unittest.TestCase):
    _FORBIDDEN_SUBSTRINGS = (
        "param_set",
        "param_set_send",
        "PARAM_SET",
        "param_ext_set",
        "set_param",
        "mission_upload",
        "upload_mission",
        "mission_",
        ".arm(",
        "takeoff(",
        "disarm",
        "set_mode",
        "set_actuator",
        "actuator",
        "offboard",
        "manual_control_send",
        "command_long_send",
        "command_long",
        "command_int",
        "mav_cmd",
    )

    _WRITE_SEND_SUFFIX = re.compile(r"\w+_send\b")

    def _executable_source(self, path: Path) -> str:
        text = path.read_text(encoding="utf-8")
        if path.name == "cli.py":
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    if node.func.attr == "add_argument":
                        for kw in node.keywords:
                            if kw.arg == "help" and isinstance(kw.value, ast.Constant):
                                kw.value = ast.Constant(value="")
                        for arg in node.args:
                            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                                arg.value = ""
                if isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(
                    node.value.value, str
                ):
                    node.value = ast.Constant(value="")
            return ast.unparse(tree).lower()
        return text.lower()

    def test_module_sources_exclude_write_and_command_apis(self) -> None:
        root = Path(__file__).resolve().parents[1] / "brain" / "preflight_params"
        sources = sorted(path for path in root.glob("**/*.py") if path.is_file())
        self.assertGreaterEqual(len(sources), 5)
        allowed_sends = ("param_request_list_send",)
        for path in sources:
            text = self._executable_source(path)
            for needle in self._FORBIDDEN_SUBSTRINGS:
                with self.subTest(path=path.name, needle=needle):
                    self.assertNotIn(needle.lower(), text, f"{needle} found in {path}")
            for match in self._WRITE_SEND_SUFFIX.findall(text):
                with self.subTest(path=path.name, send=match):
                    if match in allowed_sends:
                        continue
                    self.assertNotIn(
                        match,
                        text,
                        f"unexpected *_send call pattern {match!r} in {path}",
                    )


if __name__ == "__main__":
    unittest.main()