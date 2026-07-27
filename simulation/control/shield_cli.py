"""Run one or more shield scenarios and report what they measured.

Shadow by default. An active run needs `--mode active` typed by a person, and
the twin's own `safety.shield.enabled` is overridden only inside the scenario --
the shipped default stays off for everything else.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from simulation.control.shield_run import SCENARIOS, run_shield_scenario


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--scenario", choices=(*SCENARIOS, "all"), default="all")
    parser.add_argument("--mode", choices=("shadow", "active"), default="shadow")
    parser.add_argument("--artifact-dir", type=Path, default=Path("simulation/artifacts/control"))
    parser.add_argument("--fly-seconds", type=float, default=25.0)
    parser.add_argument("--startup-wait-s", type=float, default=32.0)
    arguments = parser.parse_args(argv)

    scenarios = SCENARIOS if arguments.scenario == "all" else (arguments.scenario,)
    reports = []
    for scenario in scenarios:
        print(f"\n=== {scenario} ({arguments.mode}) ===", flush=True)
        report = run_shield_scenario(
            scenario, arguments.artifact_dir,
            mode=arguments.mode,
            fly_seconds=arguments.fly_seconds,
            startup_wait_s=arguments.startup_wait_s,
        )
        print(json.dumps(report.as_document(), indent=2))
        reports.append(report)

    failed = [report.scenario for report in reports if not report.passed]
    print(f"\n{len(reports) - len(failed)}/{len(reports)} passed")
    if failed:
        print(f"failed: {', '.join(failed)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
