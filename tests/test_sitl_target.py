"""PX4 must report enough independent SITL indicators before the demo arms."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.sitl_target import require_px4_sitl_indication


def _drone_with_identity(
    *,
    product_name: str = "undefined",
    vendor_name: str = "undefined",
    release_name: str = "RELEASE",
    sys_autostart: int = 4001,
    sim_gz_en: int | None = 1,
    sih_vehicle_type: int | None = None,
    param_timeout: str | None = None,
) -> MagicMock:
    product = MagicMock(product_name=product_name, vendor_name=vendor_name)
    version = MagicMock(flight_sw_version_type=SimpleNamespace(name=release_name))
    info = MagicMock()
    info.get_product = AsyncMock(return_value=product)
    info.get_version = AsyncMock(return_value=version)

    async def read_param(name: str) -> int:
        if param_timeout == name:
            await asyncio.sleep(60)
        if name == "SYS_AUTOSTART":
            return sys_autostart
        if name == "SIM_GZ_EN":
            if sim_gz_en is None:
                raise RuntimeError("PARAM_UNKNOWN")
            return sim_gz_en
        if name == "SIH_VEHICLE_TYPE":
            if sih_vehicle_type is None:
                raise RuntimeError("PARAM_UNKNOWN")
            return sih_vehicle_type
        raise RuntimeError(name)

    param = MagicMock()
    param.get_param_int = AsyncMock(side_effect=read_param)
    drone = MagicMock(info=info, param=param)
    return drone


class SitlTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_sitl_like_release_profile_passes(self) -> None:
        proof = await require_px4_sitl_indication(
            _drone_with_identity(release_name="RELEASE", product_name="undefined"), timeout_s=1.0
        )
        self.assertEqual(proof["status"], "verified")
        self.assertIn("sys_autostart_posix_sitl", proof["matched_signals"])
        self.assertIn("sim_gz_en", proof["matched_signals"])

    async def test_pixhawk_like_identity_is_refused(self) -> None:
        drone = _drone_with_identity(
            product_name="PX4 Pixhawk 6X",
            vendor_name="Holybro",
            sys_autostart=0,
            sim_gz_en=None,
        )
        with self.assertRaises(DemoPointRtlError):
            await require_px4_sitl_indication(drone, timeout_s=1.0)

    async def test_param_read_timeout_is_refused(self) -> None:
        drone = _drone_with_identity(param_timeout="SYS_AUTOSTART")
        with self.assertRaises(DemoPointRtlError) as context:
            await require_px4_sitl_indication(drone, timeout_s=0.05)
        self.assertIn("SYS_AUTOSTART", str(context.exception))

    async def test_accepts_a_sitl_product_name_with_autostart(self) -> None:
        proof = await require_px4_sitl_indication(
            _drone_with_identity(product_name="PX4 SITL", release_name="STABLE", sys_autostart=4001),
            timeout_s=1.0,
        )
        self.assertEqual(proof["status"], "verified")
        self.assertGreaterEqual(len(proof["matched_signals"]), 2)

    async def test_dev_alone_is_insufficient(self) -> None:
        drone = _drone_with_identity(
            release_name="DEV",
            sys_autostart=0,
            sim_gz_en=None,
        )
        with self.assertRaises(DemoPointRtlError) as context:
            await require_px4_sitl_indication(drone, timeout_s=1.0)
        self.assertIn("DEV", str(context.exception))


if __name__ == "__main__":
    unittest.main()
