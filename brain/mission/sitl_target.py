"""PX4-side checks that the connected vehicle is a simulator."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Literal

from brain.mission.demo_errors import DemoPointRtlError

TargetVerdict = Literal["sitl_verified", "unknown", "real_suspected"]

# PX4 v1.17.0 gz / SIH SITL autostart IDs only (ROMFS init.d-posix airframes).
_GZ_AUTOSTART_RANGE = (4001, 4999)
_SIH_AUTOSTART_RANGE = (10040, 10099)
_MIN_POSITIVE_SIGNALS = 2
_SITL_PRODUCT_MARKERS = ("sitl", "sih", "simulation", "gz", "gazebo")
_SIMULATOR_SPECIFIC_SIGNALS = frozenset({"sim_gz_en", "sih_vehicle_type"})
_HARDWARE_PRODUCT_MARKERS = ("pixhawk", "holybro", "cubepilot", "cube", "durandal", "kakute", "fmuv")

# Gazebo Harmonic bridge (4001_gz_x500 sets SIM_GZ_EN default 1; gz_bridge/parameters.yaml).
_PARAM_SIM_GZ_EN = "SIM_GZ_EN"
# SIH-only params (src/modules/simulation/simulator_sih/sih_params.c).
_PARAM_SIH_VEHICLE_TYPE = "SIH_VEHICLE_TYPE"
_PARAM_SYS_AUTOSTART = "SYS_AUTOSTART"
_DEFAULT_CLASSIFIER_MAVSDK_PORT = 51052


def default_target_classifier_mavsdk_port() -> int:
    """Local gRPC port for approve-time SITL classification (loopback only)."""
    raw = os.environ.get(
        "BYTEWOLF_TARGET_CLASSIFIER_MAVSDK_PORT",
        str(_DEFAULT_CLASSIFIER_MAVSDK_PORT),
    )
    try:
        port = int(raw)
    except ValueError:
        return _DEFAULT_CLASSIFIER_MAVSDK_PORT
    if port <= 0 or port > 65535:
        return _DEFAULT_CLASSIFIER_MAVSDK_PORT
    return port


def _exception_reason(error: BaseException) -> str:
    text = str(error).strip()
    if text:
        return f"{type(error).__name__}: {text}"
    return f"{type(error).__name__} while verifying PX4 SITL identity."


def _release_type_name(version: object) -> str:
    release = getattr(version, "flight_sw_version_type", None)
    return str(getattr(release, "name", release or "")).strip().upper()


def _autostart_is_posix_sitl(autostart: int) -> bool:
    low, high = _GZ_AUTOSTART_RANGE
    if low <= autostart <= high:
        return True
    low, high = _SIH_AUTOSTART_RANGE
    return low <= autostart <= high


def collect_param_based_sitl_signals(
    *,
    sys_autostart: int,
    sim_gz_en: int | None = None,
    sih_vehicle_type: int | None = None,
) -> list[str]:
    """PX4-parameter indicators used by the multi-signal SITL gate (PR #18)."""
    matched: list[str] = []
    if _autostart_is_posix_sitl(sys_autostart):
        matched.append("sys_autostart_posix_sitl")

    gz_low, gz_high = _GZ_AUTOSTART_RANGE
    if gz_low <= sys_autostart <= gz_high and sim_gz_en is not None and sim_gz_en == 1:
        matched.append("sim_gz_en")

    sih_low, sih_high = _SIH_AUTOSTART_RANGE
    if sih_low <= sys_autostart <= sih_high and sih_vehicle_type is not None:
        if 0 <= sih_vehicle_type <= 5:
            matched.append("sih_vehicle_type")
    return list(dict.fromkeys(matched))


def sitl_indicators_sufficient(matched_signals: list[str]) -> bool:
    """At least two independent SITL indicators, or refuse."""
    unique = list(dict.fromkeys(matched_signals))
    return len(unique) >= _MIN_POSITIVE_SIGNALS


def has_simulator_specific_signal(matched_signals: list[str]) -> bool:
    """True when PX4 reports an active Gazebo or SIH simulator parameter."""
    unique = list(dict.fromkeys(matched_signals))
    return any(signal in _SIMULATOR_SPECIFIC_SIGNALS for signal in unique)


def sitl_verification_passes(matched_signals: list[str]) -> bool:
    """Multi-signal gate plus a mandatory simulator-specific parameter indicator."""
    unique = list(dict.fromkeys(matched_signals))
    return sitl_indicators_sufficient(unique) and has_simulator_specific_signal(unique)


def _suspects_real_flight_controller(
    *,
    product_name: str,
    vendor_name: str,
    release_name: str,
    sys_autostart: int,
    matched_signals: list[str],
) -> bool:
    if has_simulator_specific_signal(matched_signals):
        return False
    product = product_name.strip().lower()
    vendor = vendor_name.strip().lower()
    if any(marker in product or marker in vendor for marker in _HARDWARE_PRODUCT_MARKERS):
        return True
    if sys_autostart == 0 and release_name in {"RELEASE", "STABLE", "BETA"}:
        return True
    return False


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

    if sys_autostart == 0:
        param_observations["sys_autostart_note"] = "zero typically indicates unset hardware profile"

    sim_gz_en: int | None = None
    sih_vehicle_type: int | None = None
    gz_low, gz_high = _GZ_AUTOSTART_RANGE
    if gz_low <= sys_autostart <= gz_high:
        sim_gz_en = await _read_param_int(drone, _PARAM_SIM_GZ_EN, timeout_s=timeout_s)
        param_observations[_PARAM_SIM_GZ_EN] = str(sim_gz_en)

    sih_low, sih_high = _SIH_AUTOSTART_RANGE
    if sih_low <= sys_autostart <= sih_high:
        sih_vehicle_type = await _read_param_int(drone, _PARAM_SIH_VEHICLE_TYPE, timeout_s=timeout_s)
        param_observations[_PARAM_SIH_VEHICLE_TYPE] = str(sih_vehicle_type)

    matched.extend(
        collect_param_based_sitl_signals(
            sys_autostart=sys_autostart,
            sim_gz_en=sim_gz_en,
            sih_vehicle_type=sih_vehicle_type,
        )
    )

    unique_signals = list(dict.fromkeys(matched))
    if sitl_verification_passes(unique_signals):
        return _build_proof(
            unique_signals,
            product_name=product_name,
            vendor_name=vendor_name,
            release_name=release_name,
            sys_autostart=sys_autostart,
            param_observations=param_observations,
        )

    if not has_simulator_specific_signal(unique_signals):
        raise DemoPointRtlError(
            "PX4 did not report a simulator-specific indicator before arming "
            f"(need SIM_GZ_EN=1 or SIH_VEHICLE_TYPE in range; matched {unique_signals or ['none']}). "
            "Refusing to arm."
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


@dataclass(frozen=True)
class TargetClassification:
    """Fail-closed PX4 target verdict for approve-time and CLI gates."""

    verdict: TargetVerdict
    reason: str
    proof: dict[str, Any] | None = None


class TargetClassifier:
    """Classify the connected PX4 target without arming."""

    @staticmethod
    async def classify(drone: object, *, timeout_s: float = 10.0) -> TargetClassification:
        try:
            proof = await require_px4_sitl_indication(drone, timeout_s=timeout_s)
        except DemoPointRtlError as error:
            reason = _exception_reason(error)
            try:
                product_name, vendor_name, release_name, sys_autostart, matched = (
                    await _read_identity_and_param_signals(drone, timeout_s=timeout_s)
                )
            except DemoPointRtlError as nested:
                return TargetClassification("unknown", _exception_reason(nested) or reason)
            except (TimeoutError, asyncio.TimeoutError, RuntimeError) as nested:
                return TargetClassification("unknown", _exception_reason(nested))
            except Exception as nested:
                return TargetClassification("unknown", _exception_reason(nested))
            if _suspects_real_flight_controller(
                product_name=product_name,
                vendor_name=vendor_name,
                release_name=release_name,
                sys_autostart=sys_autostart,
                matched_signals=matched,
            ):
                return TargetClassification("real_suspected", reason)
            return TargetClassification("unknown", reason)
        except (TimeoutError, asyncio.TimeoutError, RuntimeError) as error:
            return TargetClassification("unknown", _exception_reason(error))
        except Exception as error:
            return TargetClassification("unknown", _exception_reason(error))
        return TargetClassification(
            "sitl_verified",
            "PX4 presented stock SITL indicators (simulator-specific parameter confirmed).",
            proof,
        )

    @staticmethod
    async def classify_at_endpoint(
        endpoint: str,
        *,
        mavsdk_server_port: int | None = None,
        connection_timeout_s: float = 15.0,
        classify_timeout_s: float = 10.0,
    ) -> TargetClassification:
        """Connect briefly, classify, and tear down the owned MAVSDK server."""
        from brain.cli.mavsdk_lifecycle import acquire_px4_link, stop_owned_mavsdk_server

        system = None
        try:
            from mavsdk import System
        except ModuleNotFoundError:
            return TargetClassification(
                "unknown",
                "MAVSDK is not installed; cannot verify a SITL target before arming.",
            )
        port = default_target_classifier_mavsdk_port() if mavsdk_server_port is None else mavsdk_server_port
        try:
            acquire_px4_link("plan-approve-sitl-gate")
            system = System(port=port)
            await asyncio.wait_for(system.connect(system_address=endpoint), timeout=connection_timeout_s)
            await _await_mavlink_connected(system.core, timeout_s=connection_timeout_s)
            return await TargetClassifier.classify(system, timeout_s=classify_timeout_s)
        except Exception as error:
            return TargetClassification(
                "unknown",
                f"Cannot reach PX4 for SITL identity verification: {_exception_reason(error)}",
            )
        finally:
            stop_owned_mavsdk_server(system)

    @staticmethod
    def require_sitl_verified(
        endpoint: str,
        *,
        mavsdk_server_port: int | None = None,
        connection_timeout_s: float = 15.0,
        classify_timeout_s: float = 10.0,
    ) -> TargetClassification:
        """Synchronous approve-time gate; refuses anything but sitl_verified."""
        result = asyncio.run(
            TargetClassifier.classify_at_endpoint(
                endpoint,
                mavsdk_server_port=mavsdk_server_port,
                connection_timeout_s=connection_timeout_s,
                classify_timeout_s=classify_timeout_s,
            )
        )
        return result


async def _await_mavlink_connected(core: object, *, timeout_s: float) -> None:
    async def first_connected() -> None:
        async for state in core.connection_state():
            if bool(getattr(state, "is_connected", False)):
                return
        raise DemoPointRtlError("PX4 never reported a connected MAVLink state.")

    await asyncio.wait_for(first_connected(), timeout=timeout_s)


async def _read_identity_and_param_signals(
    drone: object, *, timeout_s: float
) -> tuple[str, str, str, int, list[str]]:
    info = getattr(drone, "info", None)
    if info is None:
        raise DemoPointRtlError("MAVSDK Info is unavailable.")
    try:
        product, version = await asyncio.gather(
            asyncio.wait_for(info.get_product(), timeout=timeout_s),
            asyncio.wait_for(info.get_version(), timeout=timeout_s),
        )
    except (TimeoutError, asyncio.TimeoutError) as error:
        raise DemoPointRtlError(
            "Timed out waiting for PX4 identity telemetry before arming."
        ) from error
    except RuntimeError as error:
        raise DemoPointRtlError(f"Cannot read PX4 identity before arming: {error}") from error
    product_name = str(getattr(product, "product_name", "")).strip()
    vendor_name = str(getattr(product, "vendor_name", "")).strip()
    release_name = _release_type_name(version)
    sys_autostart = await _read_param_int(drone, _PARAM_SYS_AUTOSTART, timeout_s=timeout_s)
    matched = _collect_identity_signals(
        product_name=product_name,
        vendor_name=vendor_name,
        release_name=release_name,
    )
    sim_gz_en: int | None = None
    sih_vehicle_type: int | None = None
    gz_low, gz_high = _GZ_AUTOSTART_RANGE
    if gz_low <= sys_autostart <= gz_high:
        try:
            sim_gz_en = await _read_param_int(drone, _PARAM_SIM_GZ_EN, timeout_s=timeout_s)
        except DemoPointRtlError:
            sim_gz_en = None
    sih_low, sih_high = _SIH_AUTOSTART_RANGE
    if sih_low <= sys_autostart <= sih_high:
        try:
            sih_vehicle_type = await _read_param_int(drone, _PARAM_SIH_VEHICLE_TYPE, timeout_s=timeout_s)
        except DemoPointRtlError:
            sih_vehicle_type = None
    matched.extend(
        collect_param_based_sitl_signals(
            sys_autostart=sys_autostart,
            sim_gz_en=sim_gz_en,
            sih_vehicle_type=sih_vehicle_type,
        )
    )
    return product_name, vendor_name, release_name, sys_autostart, list(dict.fromkeys(matched))


def _coerce_optional_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _read_vehicle_identity(document: dict[str, object]) -> dict[str, object] | None:
    nested = document.get("vehicle_identity")
    if isinstance(nested, dict):
        return nested
    return None


def classify_telemetry_document(document: dict[str, object]) -> TargetVerdict:
    """Fail-closed target_class for passive dashboard telemetry (no MAVSDK param reads)."""
    identity = _read_vehicle_identity(document)
    if identity is None:
        return "unknown"

    product_name = str(identity.get("product_name", "")).strip()
    vendor_name = str(identity.get("vendor_name", "")).strip()
    release_name = str(identity.get("flight_sw_version_type", "")).strip().upper()
    sys_autostart = _coerce_optional_int(identity.get("sys_autostart"))
    sim_gz_en = _coerce_optional_int(identity.get("sim_gz_en"))
    sih_vehicle_type = _coerce_optional_int(identity.get("sih_vehicle_type"))

    matched: list[str] = []
    if product_name or vendor_name or release_name:
        matched.extend(
            _collect_identity_signals(
                product_name=product_name,
                vendor_name=vendor_name,
                release_name=release_name,
            )
        )
    if sys_autostart is not None:
        matched.extend(
            collect_param_based_sitl_signals(
                sys_autostart=sys_autostart,
                sim_gz_en=sim_gz_en,
                sih_vehicle_type=sih_vehicle_type,
            )
        )
    unique = list(dict.fromkeys(matched))
    if unique and sitl_verification_passes(unique):
        return "sitl_verified"
    product = product_name.strip().lower()
    vendor = vendor_name.strip().lower()
    if any(marker in product or marker in vendor for marker in _HARDWARE_PRODUCT_MARKERS):
        return "real_suspected"
    if sys_autostart is not None and _suspects_real_flight_controller(
        product_name=product_name,
        vendor_name=vendor_name,
        release_name=release_name,
        sys_autostart=sys_autostart,
        matched_signals=unique,
    ):
        return "real_suspected"
    return "unknown"
