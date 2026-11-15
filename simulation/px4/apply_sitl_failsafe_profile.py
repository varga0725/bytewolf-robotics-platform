"""Apply the SITL failsafe profile to a running PX4 SITL daemon (px4-param)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from simulation.gazebo.fault_injection import FaultInjectionError, PX4_BUILD_DIRECTORY, read_px4_parameter
from simulation.px4.sitl_failsafe_profile import (
    SitlFailsafeProfileError,
    apply_sitl_failsafe_profile,
    profile_applied_in_run_directory,
    verify_sitl_failsafe_profile,
    write_sitl_failsafe_status,
)


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
    parser.add_argument(
        "--px4-run-directory",
        type=Path,
        default=None,
        help="Disposable PX4 -w run directory; writes sitl-failsafe-profile.status when set.",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="Read back profile parameters (and status file when --px4-run-directory is set).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_arguments(argv)
    build_directory = args.px4_build_directory.resolve()
    run_directory = args.px4_run_directory.resolve() if args.px4_run_directory else None
    read = lambda name, directory: read_px4_parameter(name, build_directory)

    if args.verify:
        if run_directory is not None and not profile_applied_in_run_directory(run_directory):
            message = (
                f"{run_directory}/sitl-failsafe-profile.status does not record applied; "
                "run the apply step first."
            )
            print(message, file=sys.stderr)
            write_sitl_failsafe_status(run_directory, "failed", reason=message)
            return 3
        try:
            verify_sitl_failsafe_profile(px4_build_directory=build_directory, read_px4_parameter=read)
        except SitlFailsafeProfileError as error:
            print(error, file=sys.stderr)
            write_sitl_failsafe_status(run_directory, "failed", reason=str(error))
            return 2
        print("SITL failsafe profile verified on PX4.")
        return 0

    try:
        applied = apply_sitl_failsafe_profile(
            px4_build_directory=build_directory,
            read_px4_parameter=read,
        )
        write_sitl_failsafe_status(run_directory, "applied", reason=f"count={len(applied)}")
    except SitlFailsafeProfileError as error:
        print(error, file=sys.stderr)
        write_sitl_failsafe_status(run_directory, "failed", reason=str(error))
        return 2
    except FaultInjectionError as error:
        print(error, file=sys.stderr)
        write_sitl_failsafe_status(run_directory, "failed", reason=str(error))
        return 1
    print(f"Applied {len(applied)} SITL failsafe parameters.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())