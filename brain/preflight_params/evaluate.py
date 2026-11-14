"""Evaluate declarative rules against a parameter snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    actual: float | None


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
                }
                for row in self.results
            ],
        }


def _lookup(params: dict[str, float], name: str) -> float | None:
    if name in params:
        return params[name]
    # PX4 may expose uppercase-only ids; QGC exports match param names.
    for key, value in params.items():
        if key.upper() == name.upper():
            return value
    return None


def evaluate_params(
    params: dict[str, float],
    *,
    profile: PreflightCheckProfile | None = None,
) -> PreflightReport:
    """Run every rule against ``params`` (missing keys are FAIL / not readable)."""

    active = profile or PreflightCheckProfile()
    results: list[ParamCheckResult] = []
    for rule in RULES:
        results.append(_evaluate_rule(rule, params, active))
    summary = _summarize(results)
    return PreflightReport(tuple(results), summary)


def _evaluate_rule(rule: ParamRule, params: dict[str, float], profile: PreflightCheckProfile) -> ParamCheckResult:
    actual = _lookup(params, rule.param)
    status, reason = rule.check(actual, profile)
    return ParamCheckResult(
        param=rule.param,
        expected=rule.expected,
        group=rule.group,
        rationale=rule.rationale,
        status=status,
        reason=reason,
        actual=actual,
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