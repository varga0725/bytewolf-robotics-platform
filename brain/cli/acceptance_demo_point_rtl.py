"""Live demo-point-rtl acceptance entry point (avoids third-party ``tests`` package shadowing)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    loader = unittest.TestLoader()
    suite = loader.loadTestsFromName("tests.test_demo_point_rtl.DemoPointRtlAcceptanceTests")
    if suite.countTestCases() == 0:
        suite = loader.discover(
            start_dir=str(repository_root / "tests"),
            pattern="test_demo_point_rtl.py",
            top_level_dir=str(repository_root),
        )
    if suite.countTestCases() == 0:
        print("No demo-point-rtl acceptance tests were discovered.", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
