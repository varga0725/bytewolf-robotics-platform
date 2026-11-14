"""Human-readable and JSON rendering for preflight reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TextIO

from brain.preflight_params.evaluate import ParamCheckResult, PreflightReport


def format_text(report: PreflightReport) -> str:
    lines = [f"Preflight parameter check: {report.summary}", ""]
    for row in report.results:
        lines.extend(_format_result_row(row))
    return "\n".join(lines).rstrip() + "\n"


def _format_result_row(row: ParamCheckResult) -> list[str]:
    tag = row.status
    extra = " [beyond core]" if row.group == "extra" else ""
    actual = "—" if row.actual is None else f"{row.actual:g}"
    return [
        f"[{tag}]{extra} {row.param}: {row.reason}",
        f"  expected: {row.expected} | actual: {actual}",
        f"  rationale: {row.rationale}",
        "",
    ]


def write_json(report: PreflightReport, destination: Path) -> None:
    destination.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")


def print_report(report: PreflightReport, stream: TextIO | None = None) -> None:
    target = stream or __import__("sys").stdout
    target.write(format_text(report))