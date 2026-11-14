"""Evaluate declarative rules against a parameter snapshot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from brain.preflight_params.param_value import ParamValue, snapshot_numeric_values
from brain.preflight_params.profile import PreflightCheckProfile
from brain.preflight_params.rules import RULES, CheckStatus, ParamRule


@dataclass(frozen=True)
class ParamCheckResult:
    param: str
    expected: str
    group: str
    rationale: str
    status: CheckStatus
    reason: str
    actual: int | float | None
    param_type: int | None


@dataclass(frozen=True)
class PreflightReport:
    results: tuple[ParamCheckResult, ...]
    summary: str

    @property
    def has_fail(self) -> bool:
        return any(row.status == "FAIL" for row in self.results)

    @property
    def has_warn(self) -> bool:
        return any(row.status == "WARN" for row in self.results)

    @property
    def all_pass(self) -> bool:
        return not self.has_fail and not self.has_warn

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "results": [
                {
                    "param": row.param,
                    "expected": row.expected,
                    "group": row.group,
                    "rationale": row.rationale,
                    "status": row.status,
                    "reason": row.reason,
                    "actual": row.actual,
                    "param_type": row.param_type,
                }
                for row in self.results
            ],
        }


def _find_entry(params: Mapping[str, ParamValue], name: str) -> ParamValue | None:
    if name in params:
        return params[name]
    for key, entry in params.items():
        if key.upper() == name.upper():
            return entry
    return None


def evaluate_params(
    params: Mapping[str, ParamValue],
    *,
    profile: PreflightCheckProfile | None = None,
) -> PreflightReport:
    """Run every rule against ``params`` (missing keys are FAIL / not readable)."""

    active = profile or PreflightCheckProfile()
    numeric = snapshot_numeric_values(params)
    results: list[ParamCheckResult] = []
    for rule in RULES:
        results.append(_evaluate_rule(rule, params, numeric, active))
    summary = _summarize(results)
    return PreflightReport(tuple(results), summary)


def _evaluate_rule(
    rule: ParamRule,
    params: Mapping[str, ParamValue],
    numeric: dict[str, int | float],
    profile: PreflightCheckProfile,
) -> ParamCheckResult:
    entry = _find_entry(params, rule.param)
    actual_display: int | float | None
    param_type: int | None
    check_value: float | None
    if entry is not None:
        actual_display = entry.display_value
        param_type = entry.param_type
        check_value = float(actual_display)
    elif rule.param in numeric:
        actual_display = numeric[rule.param]
        param_type = None
        check_value = float(actual_display)
    else:
        actual_display = None
        param_type = None
        check_value = None
    status, reason = rule.check(check_value, profile)
    return ParamCheckResult(
        param=rule.param,
        expected=rule.expected,
        group=rule.group,
        rationale=rule.rationale,
        status=status,
        reason=reason,
        actual=actual_display,
        param_type=param_type,
    )


def _summarize(results: list[ParamCheckResult]) -> str:
    fails = sum(1 for row in results if row.status == "FAIL")
    warns = sum(1 for row in results if row.status == "WARN")
    passes = sum(1 for row in results if row.status == "PASS")
    if fails:
        return f"FAIL ({fails} failed, {warns} warn, {passes} pass)"
    if warns:
        return f"WARN ({warns} warn, {passes} pass)"
    return f"PASS ({passes} pass)"