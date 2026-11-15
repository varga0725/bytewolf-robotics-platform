"""Apply the SITL failsafe profile to a running PX4 SITL daemon (px4-param)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from simulation.gazebo.fault_injection import FaultInjectionError, PX4_BUILD_DIRECTORY, read_px4_parameter
from simulation.px4.sitl_failsafe_profile import SitlFailsafeProfileError, apply_sitl_failsafe_profile


def _parse_arguments(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Apply the ByteWolf SITL-only failsafe PX4 parameter profile.",
    )
    parser.add_argument(
        "--px4-build-directory",
        type=Path,
        default=PX4_BUILD_DIRECTORY,
        help="PX4 SITL build directory containing bin/px4-param (default: pinned tree).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_arguments(argv)
    build_directory = args.px4_build_directory.resolve()
    try:
        applied = apply_sitl_failsafe_profile(
            px4_build_directory=build_directory,
            read_px4_parameter=lambda name, directory: read_px4_parameter(name, directory),
        )
    except SitlFailsafeProfileError as error:
        print(error, file=sys.stderr)
        return 2
    except FaultInjectionError as error:
        print(error, file=sys.stderr)
        return 1
    print(f"Applied {len(applied)} SITL failsafe parameters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())