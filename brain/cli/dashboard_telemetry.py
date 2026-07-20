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
import time

from brain.cli.mavsdk_lifecycle import stop_owned_mavsdk_server
from brain.telemetry.link_lease import DEFAULT_LEASE_PATH, link_is_leased
from brain.telemetry.mavsdk_relay import MavsdkTelemetryRelay


DEFAULT_SNAPSHOT_PATH = Path("simulation/artifacts/dashboard/live-telemetry.json")


def parse_arguments(arguments: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stream PX4 telemetry into the dashboard snapshot. Reads only; commands nothing.",
    )
    parser.add_argument("--endpoint", default="udpin://0.0.0.0:14540")
    parser.add_argument("--connection-timeout", type=float, default=30.0)
    parser.add_argument("--mavsdk-server-port", type=int, default=50051)
    parser.add_argument("--snapshot-file", type=Path, default=DEFAULT_SNAPSHOT_PATH)
    parser.add_argument(
        "--link-lease", type=Path, default=DEFAULT_LEASE_PATH,
        help="Lease file a flying mission claims; the bridge yields the PX4 link while it exists.",
    )
    parser.add_argument(
        "--seconds",
        type=float,
        default=None,
        help="Stop after this many seconds. Without it the bridge runs until interrupted.",
    )
    return parser.parse_args(arguments)


async def run(arguments: argparse.Namespace) -> None:
    """Stream telemetry until stopped, standing aside while a mission flies.

    Only one MAVSDK server can bind the PX4 endpoint. Rather than race a
    mission for it — which the mission loses, silently — this waits out any
    live lease and reconnects afterwards. The flying CLI writes the same
    snapshot in the meantime, so the dashboard never goes dark.
    """
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
        await _stream_once(arguments, remaining)
        if deadline is None:
            # A relay that ended without a lease taking over is a dropped link.
            # Reconnecting is what keeps the dashboard live across a PX4 restart.
            await asyncio.sleep(1.0)
            continue
        if time.monotonic() >= deadline:
            return


async def _stream_once(arguments: argparse.Namespace, seconds: float | None) -> None:
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
        print(f"Connecting to PX4 at {arguments.endpoint}...")
        await asyncio.wait_for(system.connect(system_address=arguments.endpoint), timeout=arguments.connection_timeout)
        # The relay writes only once it has position, battery and flight state,
        # so an incomplete link leaves the previous snapshot alone rather than
        # publishing a half-known vehicle.
        relay_task = asyncio.create_task(MavsdkTelemetryRelay(system, arguments.snapshot_file).run(stop))
        print(f"Streaming telemetry into {arguments.snapshot_file}. Press Ctrl-C to stop.")
        await _relay_until_lease_or_deadline(relay_task, arguments.link_lease, seconds)
    except asyncio.CancelledError:  # pragma: no cover - interrupt path
        pass
    except (OSError, RuntimeError, TimeoutError, asyncio.TimeoutError) as error:
        # A link this process could not take is a wait, not a crash: the usual
        # reason is a mission that claimed it a moment ago.
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
    """Run the relay until a mission wants the link, or the run time is up.

    Checked once a second rather than waited on: the lease is a file another
    process writes, and a bridge that only noticed it on reconnect would keep
    the socket for the whole flight it was meant to yield.
    """
    deadline = None if seconds is None else time.monotonic() + seconds
    while not relay_task.done():
        if link_is_leased(lease_path):
            return
        if deadline is not None and time.monotonic() >= deadline:
            return
        await asyncio.wait({relay_task}, timeout=1.0)


def main(arguments: Sequence[str] | None = None) -> None:
    parsed = parse_arguments(arguments)
    try:
        asyncio.run(run(parsed))
    except KeyboardInterrupt:  # pragma: no cover - interrupt path
        print("Telemetry bridge stopped.")


if __name__ == "__main__":
    main()
