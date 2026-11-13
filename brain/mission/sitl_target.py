"""PX4-side checks that the connected vehicle is a simulator."""

from __future__ import annotations

import asyncio
import os

from brain.mission.demo_errors import DemoPointRtlError


def _assume_sitl_enabled() -> bool:
    return os.environ.get("BYTEWOLF_DEMO_ASSUME_SITL", "false").strip().lower() in {"1", "true", "yes"}


async def require_px4_sitl_indication(drone, *, timeout_s: float = 10.0) -> dict[str, str]:
    """Pre-arm refusal unless PX4 reports an unambiguous SITL/SIH indication.

    PX4 SITL and SIH builds identify themselves through the MAVSDK Info plugin:
    the product name carries ``SITL``/``SIH`` and the release type is typically
    ``DEV`` for local simulator builds. If that signal is missing — common on some
    bridge setups — the operator must set ``BYTEWOLF_DEMO_ASSUME_SITL=1`` after
    visually confirming only a simulator is connected.
    """
    if _assume_sitl_enabled():
        return {"status": "assumed", "reason": "BYTEWOLF_DEMO_ASSUME_SITL is set."}

    info = getattr(drone, "info", None)
    if info is None:
        raise DemoPointRtlError(
            "MAVSDK Info is unavailable; cannot verify a SITL target. "
            "Set BYTEWOLF_DEMO_ASSUME_SITL=1 only after confirming a simulator."
        )
    try:
        product, version = await asyncio.gather(
            asyncio.wait_for(info.get_product(), timeout=timeout_s),
            asyncio.wait_for(info.get_version(), timeout=timeout_s),
        )
    except TimeoutError as error:
        raise DemoPointRtlError(
            "Timed out waiting for PX4 identity telemetry before arming."
        ) from error
    except Exception as error:
        raise DemoPointRtlError(
            f"Cannot read PX4 identity before arming: {error}"
        ) from error

    product_name = str(getattr(product, "product_name", "")).strip().lower()
    vendor_name = str(getattr(product, "vendor_name", "")).strip().lower()
    release = getattr(version, "flight_sw_version_type", None)
    release_name = str(getattr(release, "name", release or "")).strip().upper()

    sitl_markers = ("sitl", "sih", "simulation", "gz", "gazebo")
    if any(marker in product_name for marker in sitl_markers) or any(marker in vendor_name for marker in sitl_markers):
        return {"status": "verified", "signal": "product_name", "product_name": product_name}

    if release_name == "DEV":
        return {
            "status": "verified",
            "signal": "flight_sw_version_type",
            "release_type": release_name,
            "product_name": product_name,
        }

    raise DemoPointRtlError(
        "PX4 did not report a SITL/SIH identity (product name and release type). "
        "Refusing to arm. Set BYTEWOLF_DEMO_ASSUME_SITL=1 only when a simulator is confirmed."
    )
