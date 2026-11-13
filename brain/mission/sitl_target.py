"""PX4-side checks that the connected vehicle is a simulator."""

from __future__ import annotations

import asyncio
from typing import Any

from brain.mission.demo_errors import DemoPointRtlError

# PX4 v1.17.0 gz / SIH SITL autostart IDs only (ROMFS init.d-posix airframes).
_GZ_AUTOSTART_RANGE = (4001, 4999)
_SIH_AUTOSTART_RANGE = (10040, 10099)
_MIN_POSITIVE_SIGNALS = 2
_SITL_PRODUCT_MARKERS = ("sitl", "sih", "simulation", "gz", "gazebo")

# Gazebo Harmonic bridge (4001_gz_x500 sets SIM_GZ_EN default 1; gz_bridge/parameters.yaml).
_PARAM_SIM_GZ_EN = "SIM_GZ_EN"
# SIH-only params (src/modules/simulation/simulator_sih/sih_params.c).
_PARAM_SIH_VEHICLE_TYPE = "SIH_VEHICLE_TYPE"
_PARAM_SYS_AUTOSTART = "SYS_AUTOSTART"


def _release_type_name(version: object) -> str:
    release = getattr(version, "flight_sw_version_type", None)
    return str(getattr(release, "name", release or "")).strip().upper()


def _autostart_is_posix_sitl(autostart: int) -> bool:
    low, high = _GZ_AUTOSTART_RANGE
    if low <= autostart <= high:
        return True
    low, high = _SIH_AUTOSTART_RANGE
    return low <= autostart <= high


async def _read_param_int(drone: object, name: str, *, timeout_s: float) -> int:
    param = getattr(drone, "param", None)
    if param is None:
        raise DemoPointRtlError("MAVSDK Param is unavailable; cannot verify SITL parameters.")
    try:
        return int(await asyncio.wait_for(param.get_param_int(name), timeout=timeout_s))
    except TimeoutError as error:
        raise DemoPointRtlError(f"Timed out reading PX4 parameter {name}.") from error
    except DemoPointRtlError:
        raise
    except Exception as error:
        raise DemoPointRtlError(f"Cannot read PX4 parameter {name}: {error}") from error


def _collect_identity_signals(
    *,
    product_name: str,
    vendor_name: str,
    release_name: str,
) -> list[str]:
    product = product_name.strip().lower()
    vendor = vendor_name.strip().lower()
    matched: list[str] = []
    if any(marker in product for marker in _SITL_PRODUCT_MARKERS) or any(
        marker in vendor for marker in _SITL_PRODUCT_MARKERS
    ):
        matched.append("product_name")
    if release_name == "DEV":
        matched.append("flight_sw_version_type_dev")
    return matched


def _build_proof(
    matched_signals: list[str],
    *,
    product_name: str,
    vendor_name: str,
    release_name: str,
    sys_autostart: int | None,
    param_observations: dict[str, str],
) -> dict[str, Any]:
    return {
        "status": "verified",
        "matched_signals": matched_signals,
        "product_name": product_name,
        "vendor_name": vendor_name,
        "flight_sw_version_type": release_name,
        "sys_autostart": sys_autostart,
        **param_observations,
    }


async def require_px4_sitl_indication(drone, *, timeout_s: float = 10.0) -> dict[str, Any]:
    """Pre-arm refusal unless PX4 presents at least two independent SITL indicators.

    Loopback and simulation deployment mode are enforced separately before arming.
    """
    info = getattr(drone, "info", None)
    if info is None:
        raise DemoPointRtlError(
            "MAVSDK Info is unavailable; cannot verify a SITL target before arming."
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

    product_name = str(getattr(product, "product_name", "")).strip()
    vendor_name = str(getattr(product, "vendor_name", "")).strip()
    release_name = _release_type_name(version)

    sys_autostart = await _read_param_int(drone, _PARAM_SYS_AUTOSTART, timeout_s=timeout_s)
    param_observations: dict[str, str] = {f"{_PARAM_SYS_AUTOSTART}": str(sys_autostart)}

    matched = _collect_identity_signals(
        product_name=product_name,
        vendor_name=vendor_name,
        release_name=release_name,
    )

    if _autostart_is_posix_sitl(sys_autostart):
        matched.append("sys_autostart_posix_sitl")
    elif sys_autostart == 0:
        param_observations["sys_autostart_note"] = "zero typically indicates unset hardware profile"

    low, high = _GZ_AUTOSTART_RANGE
    if low <= sys_autostart <= high:
        sim_gz_en = await _read_param_int(drone, _PARAM_SIM_GZ_EN, timeout_s=timeout_s)
        param_observations[_PARAM_SIM_GZ_EN] = str(sim_gz_en)
        if sim_gz_en == 1:
            matched.append("sim_gz_en")

    low, high = _SIH_AUTOSTART_RANGE
    if low <= sys_autostart <= high:
        sih_type = await _read_param_int(drone, _PARAM_SIH_VEHICLE_TYPE, timeout_s=timeout_s)
        param_observations[_PARAM_SIH_VEHICLE_TYPE] = str(sih_type)
        if 0 <= sih_type <= 5:
            matched.append("sih_vehicle_type")

    unique_signals = list(dict.fromkeys(matched))
    if len(unique_signals) >= _MIN_POSITIVE_SIGNALS:
        return _build_proof(
            unique_signals,
            product_name=product_name,
            vendor_name=vendor_name,
            release_name=release_name,
            sys_autostart=sys_autostart,
            param_observations=param_observations,
        )

    if release_name == "DEV" and len(unique_signals) == 1:
        raise DemoPointRtlError(
            "PX4 reported DEV build type only; that is not sufficient to prove SITL. "
            "Need at least two independent simulator indicators (e.g. SYS_AUTOSTART and SIM_GZ_EN)."
        )

    raise DemoPointRtlError(
        "PX4 did not present enough independent SITL indicators before arming "
        f"(matched {unique_signals or ['none']}). Refusing to arm."
    )
