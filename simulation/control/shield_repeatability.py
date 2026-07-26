"""Run the shield matrix repeatedly, because one pass is not a result.

The P0 gates exist because ten runs say something a single run cannot: whether a
behaviour is reliable or merely happened once. The shield is the first thing on
this platform allowed to *stop* a commanded motion, so it is held to the same
standard before it is allowed to do so for real.

Thresholds follow the P0 precedent rather than being invented here: the
scenarios that assert a safety behaviour must hold every time, and the nominal
one -- does the system work when nothing is wrong -- is allowed the same 0.9 the
P0 matrix allows. A false stop is not dangerous; it is corrosive, because it is
the reason a shield gets switched off. It is scored strictly for that reason and
leniently only in the sense that one flake does not condemn the run.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
import json
from pathlib import Path
import sys

from simulation.control.shield_run import SCENARIOS, run_shield_scenario


#: What fraction of runs must pass, per scenario. A safety behaviour that holds
#: nine times out of ten is not a safety behaviour.
THRESHOLDS = {
    "clear-path": 0.9,        # nominal: the system working when nothing is wrong
    "static-obstacle": 1.0,   # the shield's whole claim
    "blind-sector": 1.0,      # fail-closed
    "stale-sensor": 1.0,      # fail-closed
}


@dataclass(frozen=True)
class ScenarioTally:
    """How one scenario fared across the repeated runs."""

    scenario: str
    runs: int
    passed: int
    success_rate: float
    threshold: float
    met: bool
    #: The worst clearance seen in any run, which is the figure a reader wants
    #: first: the closest the vehicle ever came across everything attempted.
    worst_clearance_m: float | None = None
    #: The latest the shield ever raised the alarm. A first intervention that
    #: drifts inward across runs is the early warning that it is marginal.
    worst_first_intervention_m: float | None = None
    highest_false_stop_rate: float = 0.0
    failures: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RepeatabilityReport:
    matrix: str
    mode: str
    recorded_at: str
    runs_per_scenario: int
    tallies: list[ScenarioTally]
    passed: bool
    proof_level: str = "app+SITL"

    def as_document(self) -> dict:
        document = asdict(self)
        document["tallies"] = [asdict(tally) for tally in self.tallies]
        return document


def tally_scenario(scenario: str, reports) -> ScenarioTally:
    """Reduce one scenario's runs to a verdict. Pure, so it is unit-tested."""
    runs = list(reports)
    passed = [report for report in runs if report.passed]
    threshold = THRESHOLDS.get(scenario, 1.0)
    rate = len(passed) / len(runs) if runs else 0.0

    clearances = [
        report.minimum_clearance_m for report in runs if report.minimum_clearance_m is not None
    ]
    firsts = [
        report.clearance_at_first_intervention_m
        for report in runs
        if report.clearance_at_first_intervention_m is not None
    ]
    failures = [
        f"run {index + 1}: {'; '.join(report.findings)}"
        for index, report in enumerate(runs)
        if not report.passed
    ]
    return ScenarioTally(
        scenario=scenario,
        runs=len(runs),
        passed=len(passed),
        success_rate=round(rate, 4),
        threshold=threshold,
        met=bool(runs) and rate >= threshold,
        worst_clearance_m=min(clearances) if clearances else None,
        worst_first_intervention_m=min(firsts) if firsts else None,
        highest_false_stop_rate=max((report.false_stop_rate for report in runs), default=0.0),
        failures=failures,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=10)
    parser.add_argument("--mode", choices=("shadow", "active"), default="active")
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    parser.add_argument("--artifact-dir", type=Path, default=Path("simulation/artifacts/control"))
    parser.add_argument("--fly-seconds", type=float, default=22.0)
    arguments = parser.parse_args(argv)

    scenarios = SCENARIOS if arguments.scenario == "all" else (arguments.scenario,)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_directory = arguments.artifact_dir / f"shield-repeatability-{stamp}"

    collected: dict[str, list] = {scenario: [] for scenario in scenarios}
    for attempt in range(1, arguments.runs + 1):
        for scenario in scenarios:
            print(f"\n=== {scenario} — run {attempt}/{arguments.runs} ({arguments.mode}) ===", flush=True)
            report = run_shield_scenario(
                scenario, run_directory / f"run-{attempt:02d}",
                mode=arguments.mode, fly_seconds=arguments.fly_seconds,
            )
            collected[scenario].append(report)
            status = "pass" if report.passed else f"FAIL — {'; '.join(report.findings)}"
            print(f"  {status}", flush=True)

    tallies = [tally_scenario(scenario, collected[scenario]) for scenario in scenarios]
    report = RepeatabilityReport(
        matrix="shield.v1",
        mode=arguments.mode,
        recorded_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        runs_per_scenario=arguments.runs,
        tallies=tallies,
        passed=all(tally.met for tally in tallies),
    )
    destination = arguments.artifact_dir / f"shield-repeatability-{stamp}.json"
    destination.write_text(json.dumps(report.as_document(), indent=2) + "\n", encoding="utf-8")

    print(f"\n=== {report.matrix} ({report.mode}) ===")
    for tally in tallies:
        mark = "ok " if tally.met else "FAIL"
        print(
            f"  {mark} {tally.scenario:<18} {tally.passed}/{tally.runs} "
            f"(needs {tally.threshold:.0%})"
            + (f"  worst clearance {tally.worst_clearance_m:.2f} m" if tally.worst_clearance_m else "")
        )
    print(f"summary: {destination}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
