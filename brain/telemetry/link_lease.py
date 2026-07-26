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
import tempfile
import time


#: Anchored to the repository rather than to the caller's working directory. A
#: relative default meant the lease landed wherever the process happened to be
#: started from, so a CLI run from another directory claimed a *different* link
#: than the bridge was watching -- and neither would have noticed.
_REPO_LEASE_PATH = (
    Path(__file__).resolve().parents[2] / "simulation/artifacts/dashboard/mavlink-link.lease"
)

#: Relocates the lease for a run that must not share the repository's. Two
#: checkouts on one machine contend for the same PX4 port and should keep
#: sharing it, but a test process contends for nothing real and needs its own.
LEASE_PATH_ENV = "BYTEWOLF_LINK_LEASE"

DEFAULT_LEASE_PATH = _REPO_LEASE_PATH
# PX4's onboard MAVLink port, and the one both readers compete for.
DEFAULT_MAVLINK_PORT = 14540


def default_lease_path() -> Path:
    """Where the lease lives for this process, resolved per call.

    Read at call time, not bound as a default argument: a default argument is
    evaluated once when the module is imported, which would make the override
    below depend on import order.
    """
    override = os.environ.get(LEASE_PATH_ENV)
    return Path(override) if override else _REPO_LEASE_PATH


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


def read_lease(path: Path | None = None) -> dict[str, object] | None:
    """Return the live lease, or None when the link is free.

    A file naming a process that no longer exists is not a lease — it is
    litter from a crash, and treating it as binding would leave the dashboard
    dark until someone noticed the file.
    """
    path = path if path is not None else default_lease_path()
    try:
        document = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    pid = document.get("pid")
    if not isinstance(pid, int) or not _process_is_alive(pid):
        return None
    return document


def link_is_leased(path: Path | None = None) -> bool:
    return read_lease(path) is not None


def claim_link(owner: str, *, pid: int | None = None, path: Path | None = None) -> Path:
    """Record that `pid` owns the link. The caller must release it.

    The temporary file gets a unique name. A fixed ``.tmp`` sibling was shared
    by every process claiming the same lease, so two starting together wrote one
    file and the first ``os.replace`` moved it out from under the second, which
    then failed with FileNotFoundError instead of taking or losing the link
    cleanly. It is created in the destination's own directory so the replace
    stays on one filesystem, which is what makes it atomic.
    """
    path = path if path is not None else default_lease_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {"owner": owner, "pid": int(pid if pid is not None else os.getpid())}
    handle, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(document) + "\n")
        os.replace(temporary, path)
    except BaseException:
        # A half-written claim must not be left behind: the next reader would
        # find litter in the directory it scans for its own lease.
        temporary.unlink(missing_ok=True)
        raise
    return path


@contextmanager
def lease_link(owner: str, *, pid: int | None = None, path: Path | None = None) -> Iterator[Path]:
    """Hold the link for one mission, and give it back however the block ends.

    Released on the way out of the block whatever happened inside, because a
    lease that outlives its mission is exactly the failure this replaces.
    """
    claimed = claim_link(owner, pid=pid, path=path)
    try:
        yield claimed
    finally:
        release_link(claimed)


def release_link(path: Path | None = None) -> None:
    path = path if path is not None else default_lease_path()
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def release_link_if_mine(path: Path | None = None) -> None:
    """Give back only a lease this process took.

    Release points are shared with readers that never claim anything — the
    telemetry bridge tears down its MAVSDK server through the same helper a
    mission does. An unconditional release there would hand a flying mission's
    link to whoever asked next.
    """
    path = path if path is not None else default_lease_path()
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
