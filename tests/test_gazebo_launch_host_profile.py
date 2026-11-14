"""Linux hosts must not require Homebrew Qt for PX4/Gazebo launchers."""

from __future__ import annotations

import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_LAUNCH = _ROOT / "simulation" / "gazebo" / "launch"


class GazeboLaunchHostProfileTests(unittest.TestCase):
    def test_validate_checks_qt_only_on_darwin(self) -> None:
        source = (_LAUNCH / "validate_px4_gazebo.zsh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$(uname)" == "Darwin" ]]; then', source)
        self.assertNotIn(
            'command -v brew >/dev/null 2>&1 && [[ ! -d "$(brew --prefix qt@5',
            source.split('if [[ "$(uname)" == "Darwin" ]]; then', 1)[0],
        )

    def test_headless_launcher_sets_cmake_prefix_from_qt_only_on_darwin(self) -> None:
        source = (_LAUNCH / "run_px4_gazebo_headless.zsh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then', source)
        self.assertNotIn('CMAKE_PREFIX_PATH="$(brew --prefix qt@5)', source)

    def test_interactive_launcher_sets_cmake_prefix_from_qt_only_on_darwin(self) -> None:
        source = (_LAUNCH / "run_px4_gazebo.zsh").read_text(encoding="utf-8")
        self.assertIn('if [[ "$(uname)" == "Darwin" ]] && command -v brew >/dev/null 2>&1; then', source)
        self.assertNotIn('CMAKE_PREFIX_PATH="$(brew --prefix qt@5)', source)


if __name__ == "__main__":
    unittest.main()
