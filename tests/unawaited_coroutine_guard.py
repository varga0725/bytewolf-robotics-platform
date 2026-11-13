"""Detect coroutines that were created but never awaited (destructor / GC path)."""

from __future__ import annotations

import gc
import sys
import threading
import warnings
from typing import Any

_LOCK = threading.Lock()
_VIOLATIONS: list[str] = []
_INSTALLED = False
_FAIL_FAST = False
_ORIGINAL_HOOK = sys.unraisablehook
_ORIGINAL_SHOWWARNING = warnings.showwarning
_ORIGINAL_TEST_RUN = None
_TLS = threading.local()


def install(*, fail_fast: bool = False) -> None:
    global _INSTALLED, _FAIL_FAST
    _FAIL_FAST = fail_fast
    if _INSTALLED:
        return
    warnings.filterwarnings("always", category=RuntimeWarning)
    warnings.showwarning = _showwarning  # type: ignore[assignment]
    sys.unraisablehook = _hook
    _wrap_unittest_run()
    _INSTALLED = True


def _wrap_unittest_run() -> None:
    import unittest

    global _ORIGINAL_TEST_RUN
    _ORIGINAL_TEST_RUN = unittest.TestCase.run

    def run(self, result=None):  # type: ignore[no-untyped-def]
        _begin_test(self)
        try:
            return _ORIGINAL_TEST_RUN(self, result)
        finally:
            _end_test(self)

    unittest.TestCase.run = run  # type: ignore[method-assign]


def _begin_test(test_case: object) -> None:
    _TLS.needs_gc = False
    _TLS.test_case = test_case


def _end_test(test_case: object) -> None:
    try:
        check_after_gc(test_case)
    finally:
        _TLS.test_case = None


def clear() -> None:
    with _LOCK:
        _VIOLATIONS.clear()


def violations() -> tuple[str, ...]:
    with _LOCK:
        return tuple(_VIOLATIONS)


def check_after_gc(test_case: object | None = None) -> None:
    import unittest

    if test_case is None:
        test_case = getattr(_TLS, "test_case", None)
    needs_gc = bool(getattr(_TLS, "needs_gc", False))
    if isinstance(test_case, unittest.IsolatedAsyncioTestCase):
        needs_gc = True
    pending = violations()
    if not needs_gc and not pending:
        return
    gc.collect()
    pending = violations()
    if pending:
        message = "Unawaited coroutine(s): " + "; ".join(pending)
        clear()
        raise AssertionError(message)
    _TLS.needs_gc = False


def _mark_needs_gc() -> None:
    _TLS.needs_gc = True


def _record_unawaited(message: str) -> None:
    _mark_needs_gc()
    with _LOCK:
        _VIOLATIONS.append(message)
    if _FAIL_FAST:
        raise RuntimeError(message)


def _showwarning(
    message: Warning | str,
    category: type[Warning],
    filename: str,
    lineno: int,
    file: Any = None,
    line: str | None = None,
) -> None:
    text = str(message)
    if category is RuntimeWarning and "never awaited" in text.lower():
        _record_unawaited(text)
    if _ORIGINAL_SHOWWARNING is not None:
        _ORIGINAL_SHOWWARNING(message, category, filename, lineno, file, line)


def _hook(unraisable: Any) -> None:
    message = unraisable.err_msg or ""
    if "never awaited" not in message.lower() and "coroutine" not in message.lower():
        if _ORIGINAL_HOOK is not None:
            _ORIGINAL_HOOK(unraisable)
        return
    _record_unawaited(message or "coroutine was never awaited")
    if _ORIGINAL_HOOK is not None:
        _ORIGINAL_HOOK(unraisable)
