"""Keep the dashboard live for as long as the simulator runs.

Until now the only writers of `live-telemetry.json` were the flight CLIs, so
the dashboard saw the vehicle **only while a mission was executing**. Start the
simulator, open the dashboard, and it showed yesterday's snapshot — which reads
as "the app does not see the drone", and it was right.

This bridge does one thing: connect to PX4's MAVLink endpoint and run the same
read-only relay the missions use, until it is stopped. It holds no mission, no
SafetyGate and no adapter, and MAVSDK's action API is never imported here — a
telemetry bridge that could arm something would be a control path wearing a
monitoring badge.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from pathlib import Path
import sys
import time

from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from brain.safety.deployment import add_deployment_arguments, authorize_readonly_connection
from brain.telemetry.link_lease import default_lease_path, link_is_leased
from brain.telemetry.loopback_endpoint import LoopbackEndpointError, require_loopback_udpin_endpoint
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay
from brain.telemetry.pymavlink_relay import run_pymavlink_relay


DEFAULT_SNAPSHOT_PATH = Path("simulation/artifacts/dashboard/live-telemetry.json")
DEFAULT_BACKEND = "pymavlink"
BACKEND_CHOICES = ("pymavlink", "mavsdk")


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream PX4 telemetry into the dashboard snapshot. Reads only; commands nothing.",
    )
    parser.add_argument("--endpoint", default="udpin://127.0.0.1:14540")
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default=DEFAULT_BACKEND,
        help=(
            "Telemetry transport. pymavlink (default) is passive UDP ingest on loopback only. "
            "mavsdk spawns mavsdk_server, which always exposes an insecure gRPC API on 0.0.0.0 "
            "(see docs/mavsdk-server-grpc-listen-address.md)."
        ),
    )
    parser.add_argument(
        "--write-interval",
        type=float,
        default=0.2,
        help="Minimum seconds between snapshot disk writes for core telemetry (default 0.2).",
    )
    parser.add_argument("--connection-timeout", type=float, default=30.0)
    parser.add_argument(
        "--mavsdk-server-port",
        type=int,
        default=50051,
        help="gRPC port when --backend mavsdk (server still listens on all interfaces).",
    )
    parser.add_argument("--snapshot-file", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument(
        "--link-lease", type=Path, default=default_lease_path(),
        help="Lease file a flying mission claims; the bridge yields the PX4 link while it exists.",
    )
    parser.add_argument(
        "--allow-link-lease-holder-pid",
        type=int,
        action="append",
        default=None,
        help="PIDs whose link lease this reader may share (e.g. parent acceptance harness).",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Stop after this many seconds. Without it the bridge runs until interrupted.",
    )
    add_deployment_arguments(parser, actuation_capable=False)
    return parser.parse_args(arguments)


def main(arguments: Sequence[str] | None = None) -> None:
    parsed = parse_arguments(arguments)
    try:
        if parsed.backend == "pymavlink":
            require_loopback_udpin_endpoint(parsed.endpoint)
        authorize_readonly_connection(parsed.deployment_mode, parsed.endpoint)
        if parsed.backend == "pymavlink":
            run_pymavlink_bridge(parsed)
            return
        asyncio.run(run_mavsdk(parsed))
    except LoopbackEndpointError as error:
        print(f"{error}", file=sys.stderr)
        sys.exit(2)
    except KeyboardInterrupt:  # pragma: no cover - interrupt path
        print("Telemetry bridge stopped.")


def _allowed_lease_holders(arguments: argparse.Namespace) -> frozenset[int]:
    raw = arguments.allow_link_lease_holder_pid
    if not raw:
        return frozenset()
    return frozenset(int(item) for item in raw)


def run_pymavlink_bridge(arguments: argparse.Namespace) -> None:
    """Passive pymavlink relay with the same lease yield semantics as MAVSDK."""

    holders = _allowed_lease_holders(arguments)
    deadline = None if arguments.seconds is None else time.monotonic() + arguments.seconds
    announced_wait = False
    while True:
        if link_is_leased(arguments.link_lease, allow_holder_pids=holders):
            if not announced_wait:
                print("A mission holds the PX4 link; the bridge is standing by.")
                announced_wait = True
            if deadline is not None and time.monotonic() >= deadline:
                return
            time.sleep(1.0)
            continue
        announced_wait = False
        remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        if remaining == 0.0:
            return
        try:
            print(f"Connecting to PX4 at {arguments.endpoint} (pymavlink, read-only)...")
            print(f"Streaming telemetry into {arguments.snapshot_file}. Press Ctrl-C to stop.")
            _stream_pymavlink_once(arguments, remaining)
        except (OSError, RuntimeError, ValueError) as error:
            print(f"PX4 link unavailable: {type(error).__name__}: {error}")
        if deadline is None:
            time.sleep(1.0)
            continue
        if time.monotonic() >= deadline:
            return


def _stream_pymavlink_once(arguments: argparse.Namespace, seconds: float | None) -> None:
    deadline = None if seconds is None else time.monotonic() + seconds

    def should_stop() -> bool:
        return deadline is not None and time.monotonic() >= deadline

    run_pymavlink_relay(
        arguments.endpoint,
        arguments.snapshot_file,
        write_interval_s=arguments.write_interval,
        link_lease=arguments.link_lease,
        allow_link_lease_holder_pids=_allowed_lease_holders(arguments),
        should_stop=should_stop,
    )


async def run_mavsdk(arguments: argparse.Namespace) -> None:
    """Stream telemetry until stopped, standing aside while a mission flies."""
    deadline = None if arguments.seconds is None else time.monotonic() + arguments.seconds
    announced_wait = False
    while True:
        if link_is_leased(arguments.link_lease):
            if not announced_wait:
                print("A mission holds the PX4 link; the bridge is standing by.")
                announced_wait = True
            if deadline is not None and time.monotonic() >= deadline:
                return
            await asyncio.sleep(1.0)
            continue
        announced_wait = False
        remaining = None if deadline is None else max(deadline - time.monotonic(), 0.0)
        if remaining == 0.0:
            return
        await _stream_mavsdk_once(arguments, remaining)
        if deadline is None:
            await asyncio.sleep(1.0)
            continue
        if time.monotonic() >= deadline:
            return


async def _stream_mavsdk_once(arguments: argparse.Namespace, seconds: float | None) -> None:
    try:
        from mavsdk import System
    except ModuleNotFoundError as error:  # pragma: no cover - environment guard
        raise RuntimeError(
            "MAVSDK is not installed. Run: .venv/bin/pip install -r requirements.txt"
        ) from error

    system = System(port=arguments.mavsdk_server_port)
    stop = asyncio.Event()
    relay_task: asyncio.Task[None] | None = None
    try:
        print(f"Connecting to PX4 at {arguments.endpoint} (mavsdk; gRPC on 0.0.0.0:{arguments.mavsdk_server_port})...")
        await asyncio.wait_for(system.connect(system_address=arguments.endpoint), timeout=arguments.connection_timeout)
        relay_task = asyncio.create_task(MavsdkTelemetryRelay(system, arguments.snapshot_file).run(stop))
        print(f"Streaming telemetry into {arguments.snapshot_file}. Press Ctrl-C to stop.")
        await _relay_until_lease_or_deadline(relay_task, arguments.link_lease, seconds)
    except asyncio.CancelledError:  # pragma: no cover - interrupt path
        pass
    except (OSError, RuntimeError, TimeoutError, asyncio.TimeoutError) as error:
        print(f"PX4 link unavailable: {type(error).__name__}: {error}")
    finally:
        stop.set()
        if relay_task is not None:
            relay_task.cancel()
            try:
                await relay_task
            except (asyncio.CancelledError, Exception) as error:  # noqa: BLE001 - reported, never raised
                if not isinstance(error, asyncio.CancelledError):
                    print(f"Telemetry relay stopped: {type(error).__name__}: {error}")
        stop_owned_mavsdk_server(system)


async def _relay_until_lease_or_deadline(
    relay_task: asyncio.Task[None], lease_path: Path, seconds: float | None
) -> None:
    """Run the relay until a mission wants the link, or the run time is up."""
    deadline = None if seconds is None else time.monotonic() + seconds
    while not relay_task.done():
        if link_is_leased(lease_path):
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        await asyncio.wait({relay_task}, timeout=1.0)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # pragma: no cover - interrupt path
        print("Telemetry bridge stopped.")