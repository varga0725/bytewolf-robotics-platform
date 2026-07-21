"""Decide which process owns the PX4 MAVLink endpoint, one at a time.

Only one MAVSDK server can bind `udpin://0.0.0.0:14540`. The dashboard's
telemetry bridge holds it so the map stays live while the simulator runs, and a
mission CLI needs it to fly. Until now the two simply collided: with the bridge
running, an approved mission's subprocess could not bind the port and died —
into a `DEVNULL` pipe, so the dashboard reported a mission submitted and nothing
ever moved.

This is the smallest thing that fixes it honestly: a lease file. A mission
claims the link before it starts and releases it when its process ends; the
bridge treats a claimed link as a signal to disconnect and wait, then reconnects
by itself once the flight is over. The mission CLI writes the same telemetry
snapshot the bridge does, so the dashboard keeps seeing the vehicle throughout —
the handover is invisible from the browser.

The lease grants nothing and enforces nothing. It cannot arm, command, or
prevent a flight; it only says which reader should currently hold the socket.
PX4 remains the safety authority regardless of what this file says.

A stale lease is bounded rather than trusted forever: it records the owning
process, and a lease whose process is gone is treated as released. A crashed
mission must not leave the dashboard permanently blind.
"""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Callable, Iterator
import json
import os
from pathlib import Path
import socket
import time


DEFAULT_LEASE_PATH = Path("simulation/artifacts/dashboard/mavlink-link.lease")
# PX4's onboard MAVLink port, and the one both readers compete for.
DEFAULT_MAVLINK_PORT = 14540


def _process_is_alive(pid: int) -> bool:
    # Signal 0 to a non-positive pid addresses a process *group*, not a process,
    # and would report almost any lease as live. No real owner is ever <= 0.
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # Alive, owned by someone else.
        return True
    return True


def read_lease(path: Path = DEFAULT_LEASE_PATH) -> dict[str, object] | None:
    """Return the live lease, or None when the link is free.

    A file naming a process that no longer exists is not a lease — it is
    litter from a crash, and treating it as binding would leave the dashboard
    dark until someone noticed the file.
    """
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = document.get("pid")
    if not isinstance(pid, int) or not _process_is_alive(pid):
        return None
    return document


def link_is_leased(path: Path = DEFAULT_LEASE_PATH) -> bool:
    return read_lease(path) is not None


def claim_link(owner: str, *, pid: int | None = None, path: Path = DEFAULT_LEASE_PATH) -> Path:
    """Record that `pid` owns the link. The caller must release it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"owner": owner, "pid": int(pid if pid is not None else os.getpid())}
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(document) + "\n")
    os.replace(temporary, path)
    return path


@contextmanager
def lease_link(owner: str, *, pid: int | None = None, path: Path = DEFAULT_LEASE_PATH) -> Iterator[Path]:
    """Hold the link for one mission, and give it back however the block ends.

    Released on the way out of the block whatever happened inside, because a
    lease that outlives its mission is exactly the failure this replaces.
    """
    claim_link(owner, pid=pid, path=path)
    try:
        yield path
    finally:
        release_link(path)


def release_link(path: Path = DEFAULT_LEASE_PATH) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def release_link_if_mine(path: Path = DEFAULT_LEASE_PATH) -> None:
    """Give back only a lease this process took.

    Release points are shared with readers that never claim anything — the
    telemetry bridge tears down its MAVSDK server through the same helper a
    mission does. An unconditional release there would hand a flying mission's
    link to whoever asked next.
    """
    lease = read_lease(path)
    if lease is not None and lease.get("pid") == os.getpid():
        release_link(path)


def wait_for_free_link(
    *, port: int = DEFAULT_MAVLINK_PORT, timeout_s: float = 15.0, sleep: Callable[[float], None] = time.sleep
) -> bool:
    """Wait until the MAVLink port can actually be bound, or give up saying so.

    Claiming the lease only asks the bridge to leave; it does not wait for it to
    finish leaving. MAVSDK's server does not retry a failed bind — it reports
    "Address already in use" once and the mission is over — so the half second
    between the request and the release was enough to lose every flight started
    while the bridge was up.

    Binding is the check because it is the same question the mission is about to
    ask. A poll of the process table would answer a different one.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.bind(("0.0.0.0", port))
            return True
        except OSError:
            pass
        finally:
            probe.close()
        if time.monotonic() >= deadline:
            return False
        sleep(0.1)
