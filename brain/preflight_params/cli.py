"""Command-line entry for read-only preflight parameter checks."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from math import isfinite
from pathlib import Path
import sys

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.mavlink import MavlinkReadError
from brain.preflight_params.profile import PreflightCheckProfile
from brain.preflight_params.report import print_report, write_json
from brain.preflight_params.sources import load_params_from_connection, parse_qgc_params_file
from brain.safety.deployment import add_deployment_arguments, authorize_readonly_connection


def _positive_seconds(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.") from error
    if not isfinite(seconds) or seconds <= 0.0:
        raise argparse.ArgumentTypeError("Timeout must be a positive finite number of seconds.")
    return seconds


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Read-only PX4 preflight parameter checker (no writes, no arm, no mission upload).",
    )
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--connection",
        metavar="URL",
        help="MAVLink connection (e.g. udpin://0.0.0.0:14540). Read-only parameter download.",
    )
    source.add_argument(
        "--params-file",
        type=Path,
        metavar="PATH",
        help="QGroundControl .params export to evaluate offline.",
    )
    parser.add_argument(
        "--json",
        type=Path,
        metavar="OUT.json",
        default=None,
        help="Optional path to write the structured report.",
    )
    parser.add_argument(
        "--accept-nav-dll-land",
        action="store_true",
        help="Treat NAV_DLL_ACT=3 (Land) as acceptable instead of requiring Return (2).",
    )
    parser.add_argument(
        "--timeout",
        type=_positive_seconds,
        default=60.0,
        help="Seconds to wait for MAVLink heartbeat and parameter download.",
    )
    add_deployment_arguments(parser, actuation_capable=False)
    return parser.parse_args(arguments)


def exit_code_for_report(report: object, *, read_error: bool) -> int:
    if read_error:
        return 2
    if getattr(report, "has_fail", False):
        return 1
    return 0


def run(arguments: argparse.Namespace) -> int:
    profile = PreflightCheckProfile(accept_nav_dll_land=arguments.accept_nav_dll_land)
    try:
        if arguments.params_file is not None:
            params = parse_qgc_params_file(arguments.params_file)
        else:
            authorize_readonly_connection(arguments.deployment_mode, arguments.connection)
            params = load_params_from_connection(arguments.connection, timeout_s=arguments.timeout)
    except (MavlinkReadError, OSError, ValueError) as error:
        print(f"read error: {error}", file=sys.stderr)
        return 2

    report = evaluate_params(params, profile=profile)
    print_report(report)
    if arguments.json is not None:
        write_json(report, arguments.json)
    return exit_code_for_report(report, read_error=False)


def main(arguments: Sequence[str] | None = None) -> None:
    sys.exit(run(parse_arguments(arguments)))