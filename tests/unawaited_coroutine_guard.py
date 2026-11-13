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


def install(*, fail_fast: bool = False) -> None:
    import warnings

    global _INSTALLED, _FAIL_FAST
    _FAIL_FAST = fail_fast
    if _INSTALLED:
        return
    warnings.filterwarnings("always", category=RuntimeWarning)
    warnings.showwarning = _showwarning  # type: ignore[assignment]
    sys.unraisablehook = _hook
    _wrap_unittest_teardown()
    _INSTALLED = True


def _wrap_unittest_teardown() -> None:
    import unittest

    original = unittest.TestCase.tearDown

    def guarded(self) -> None:  # type: ignore[no-untyped-def]
        try:
            original(self)
        finally:
            check_after_gc()

    unittest.TestCase.tearDown = guarded  # type: ignore[method-assign]


def clear() -> None:
    with _LOCK:
        _VIOLATIONS.clear()


def violations() -> tuple[str, ...]:
    with _LOCK:
        return tuple(_VIOLATIONS)


def check_after_gc() -> None:
    gc.collect()
    gc.collect()
    pending = violations()
    if pending:
        raise AssertionError("Unawaited coroutine(s): " + "; ".join(pending))


def _record_unawaited(message: str) -> None:
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


install()
