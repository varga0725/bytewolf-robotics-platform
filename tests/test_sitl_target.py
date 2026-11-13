"""PX4 must report a SITL identity before the demo arms."""

from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.sitl_target import require_px4_sitl_indication


class SitlTargetTests(unittest.IsolatedAsyncioTestCase):
    async def test_refuses_when_px4_does_not_report_sitl(self) -> None:
        product = MagicMock(product_name="Holybro X500", vendor_name="Holybro")
        version = MagicMock(flight_sw_version_type=MagicMock(name="STABLE"))
        info = MagicMock()
        info.get_product = AsyncMock(return_value=product)
        info.get_version = AsyncMock(return_value=version)
        drone = MagicMock(info=info)

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(DemoPointRtlError):
                await require_px4_sitl_indication(drone, timeout_s=1.0)

    async def test_accepts_a_sitl_product_name(self) -> None:
        product = MagicMock(product_name="PX4 SITL", vendor_name="PX4")
        version = MagicMock(flight_sw_version_type=MagicMock(name="STABLE"))
        info = MagicMock()
        info.get_product = AsyncMock(return_value=product)
        info.get_version = AsyncMock(return_value=version)
        drone = MagicMock(info=info)

        proof = await require_px4_sitl_indication(drone, timeout_s=1.0)
        self.assertEqual(proof["status"], "verified")

    @patch.dict(os.environ, {"BYTEWOLF_DEMO_ASSUME_SITL": "1"})
    async def test_assume_flag_skips_verification(self) -> None:
        proof = await require_px4_sitl_indication(MagicMock(), timeout_s=1.0)
        self.assertEqual(proof["status"], "assumed")


if __name__ == "__main__":
    unittest.main()
