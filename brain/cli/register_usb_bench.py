"""Explicitly add verified USB bench evidence to the local fleet registry."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

from brain.fleet.registry import record_usb_bench_observation


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Register existing USB bench evidence; it never connects to PX4.")
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--artifact-root", type=Path, default=Path("simulation/artifacts/hardware-bench"))
    parser.add_argument("--registry", type=Path, default=Path("var/fleet/registry.json"))
    parser.add_argument("--platform-id", default=None, help="Permanent UUID; generated only for a new physical robot.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--kind", required=True, help="e.g. aerial_multirotor, ground_robot, humanoid")
    parser.add_argument("--twin-id", default=None)
    return parser.parse_args(arguments)


def run(arguments: argparse.Namespace) -> str:
    platform_id = arguments.platform_id or str(uuid4())
    registry = record_usb_bench_observation(
        arguments.registry, arguments.artifact, artifact_root=arguments.artifact_root,
        platform_id=platform_id, display_name=arguments.name, kind=arguments.kind, twin_id=arguments.twin_id,
    )
    print(f"Registered {platform_id} in {arguments.registry} ({len(registry.robots)} fleet robot(s)).")
    return platform_id


def main(arguments: Sequence[str] | None = None) -> None:
    run(parse_arguments(arguments))


if __name__ == "__main__":
    main()
