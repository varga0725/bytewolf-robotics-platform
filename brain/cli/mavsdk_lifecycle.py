"""Compatibility boundary for the MAVSDK server owned by a CLI invocation."""

from __future__ import annotations

import os
from typing import Protocol

from brain.telemetry.link_lease import (
    claim_link,
    release_link,
    release_link_if_mine,
    wait_for_free_link,
)


class MavsdkSystem(Protocol):
    """The server lifecycle hook exposed by the installed MAVSDK runtime."""

    def _stop_mavsdk_server(self) -> None: ...


def stop_owned_mavsdk_server(system: MavsdkSystem | None) -> None:
    """Stop MAVSDK's child server, and give back the link if this run took it.

    Every CLI already calls this in its `finally`, which makes it the one place
    a run reliably reaches on the way out — success, refusal or crash. The
    release is conditional because the telemetry bridge tears its own server
    down through here too, and it never claims a lease: releasing
    unconditionally would hand a flying mission's link to whoever asked next.
    """
    if system is not None:
        system._stop_mavsdk_server()
    release_link_if_mine()


class Px4LinkUnavailableError(RuntimeError):
    """Another process holds the PX4 endpoint, so this run commanded nothing."""


def acquire_px4_link(owner: str, *, timeout_s: float = 15.0) -> None:
    """Take the PX4 endpoint for this run before MAVSDK tries to bind it.

    Only one MAVSDK server can bind the port. The dashboard's telemetry bridge
    holds it so the map stays live between flights, and it steps aside for a
    claimed lease — but only the gateway was claiming one, so a CLI run by hand
    while the bridge was up still died on "Address already in use". MAVSDK does
    not retry a failed bind, so that is the whole flight, over before it starts.

    Claiming asks the holder to leave; the wait is what confirms it has. A link
    that never frees is refused here rather than left to become a connection
    timeout half a minute later, because those two failures need different
    answers from whoever is watching.
    """
    claim_link(owner, pid=os.getpid())
    if not wait_for_free_link(timeout_s=timeout_s):
        release_link()
        raise Px4LinkUnavailableError(
            "The PX4 endpoint is still held by another process. Stop the telemetry "
            "bridge or the other mission, then try again; nothing was commanded."
        )
