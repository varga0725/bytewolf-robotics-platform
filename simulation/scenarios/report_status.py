"""Process exit semantics for headless P0 reports."""

from __future__ import annotations

import json
from pathlib import Path


def report_exit_code(report_path: Path) -> int:
    """Return the process exit code callers should use for a written P0 report."""
    document = json.loads(report_path.read_text(encoding="utf-8"))
    return 0 if document.get("overall_status") == "passed" else 1
