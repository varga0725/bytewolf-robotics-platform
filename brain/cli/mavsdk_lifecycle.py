"""Compatibility boundary for the MAVSDK server owned by a CLI invocation."""

from __future__ import annotations

from typing import Protocol


class MavsdkSystem(Protocol):
    """The server lifecycle hook exposed by the installed MAVSDK runtime."""

    def _stop_mavsdk_server(self) -> None: ...


def stop_owned_mavsdk_server(system: MavsdkSystem | None) -> None:
    """Stop MAVSDK's child server so a completed CLI cannot poison later runs."""
    if system is not None:
        system._stop_mavsdk_server()
