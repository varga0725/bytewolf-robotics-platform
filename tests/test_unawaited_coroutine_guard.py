"""Meta-tests for the unawaited-coroutine guard."""

from __future__ import annotations

import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class UnawaitedCoroutineGuardTests(unittest.TestCase):
    def test_subprocess_detects_intentionally_unawaited_coroutine(self) -> None:
        script = textwrap.dedent(
            f"""
            import asyncio
            import os
            import sys
            import unittest

            os.chdir({str(ROOT)!r})
            sys.path.insert(0, {str(ROOT)!r})
            import gc
            import tests  # installs guard

            class LeakTest(unittest.IsolatedAsyncioTestCase):
                async def test_leaves_coroutine_unawaited(self) -> None:
                    async def _coro() -> None:
                        return None

                    _coro()
                    gc.collect()
                    await asyncio.sleep(0)

            if __name__ == "__main__":
                unittest.main(verbosity=0, exit=True)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertNotEqual(completed.returncode, 0)
        combined = completed.stdout + completed.stderr
        self.assertTrue(
            "Unawaited coroutine" in combined or "never awaited" in combined,
            msg=combined,
        )

    def test_subprocess_runs_guard_when_tear_down_skips_super(self) -> None:
        script = textwrap.dedent(
            f"""
            import gc
            import os
            import sys
            import unittest

            os.chdir({str(ROOT)!r})
            sys.path.insert(0, {str(ROOT)!r})
            import tests

            class LeakWithoutSuperTearDown(unittest.TestCase):
                def tearDown(self) -> None:
                    pass

                def test_leaves_coroutine_unawaited(self) -> None:
                    async def _coro() -> None:
                        return None

                    _coro()
                    gc.collect()

            if __name__ == "__main__":
                unittest.main(verbosity=0, exit=True)
            """
        )
        completed = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            cwd=ROOT,
        )
        self.assertNotEqual(completed.returncode, 0)
        combined = completed.stdout + completed.stderr
        self.assertTrue(
            "Unawaited coroutine" in combined or "never awaited" in combined,
            msg=combined,
        )


if __name__ == "__main__":
    unittest.main()
