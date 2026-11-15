"""Build the dashboard ``vehicle_identity`` block from passive PX4 parameter reads."""

from __future__ import annotations

from brain.mission.sitl_target import _GZ_AUTOSTART_RANGE, _SIH_AUTOSTART_RANGE


def vehicle_identity_from_params(
    *,
    sys_autostart: int | None,
    sim_gz_en: int | None = None,
    sih_vehicle_type: int | None = None,
) -> dict[str, object] | None:
    """Shape expected by ``classify_telemetry_document`` (nested ints, fail-closed when absent)."""
    if sys_autostart is None:
        return None
    identity: dict[str, object] = {"sys_autostart": sys_autostart}
    gz_low, gz_high = _GZ_AUTOSTART_RANGE
    if gz_low <= sys_autostart <= gz_high and sim_gz_en is not None:
        identity["sim_gz_en"] = sim_gz_en
    sih_low, sih_high = _SIH_AUTOSTART_RANGE
    if sih_low <= sys_autostart <= sih_high and sih_vehicle_type is not None:
        identity["sih_vehicle_type"] = sih_vehicle_type
    return identity


def follow_up_param_reads(sys_autostart: int | None) -> tuple[str, ...]:
    """PARAM_REQUEST_READ names to issue after SYS_AUTOSTART is known."""
    if sys_autostart is None:
        return ()
    gz_low, gz_high = _GZ_AUTOSTART_RANGE
    if gz_low <= sys_autostart <= gz_high:
        return (_PARAM_SIM_GZ_EN,)
    sih_low, sih_high = _SIH_AUTOSTART_RANGE
    if sih_low <= sys_autostart <= sih_high:
        return (_PARAM_SIH_VEHICLE_TYPE,)
    return ()


__all__ = ["follow_up_param_reads", "vehicle_identity_from_params"]