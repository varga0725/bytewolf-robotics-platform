"""PX4 must report enough independent SITL indicators before the demo arms."""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from brain.mission.demo_point_rtl import DemoPointRtlError
from brain.mission.sitl_target import (
    TargetClassifier,
    _DEFAULT_CLASSIFIER_MAVSDK_PORT,
    classify_telemetry_document,
    default_target_classifier_mavsdk_port,
    require_px4_sitl_indication,
)


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


class TargetClassifierPortEnvTests(unittest.TestCase):
    def test_valid_port_env_is_used(self) -> None:
        with patch.dict(os.environ, {"BYTEWOLF_TARGET_CLASSIFIER_MAVSDK_PORT": "52001"}, clear=False):
            self.assertEqual(default_target_classifier_mavsdk_port(), 52001)

    def test_invalid_port_values_fall_back_to_default(self) -> None:
        for raw in ("abc", "0", "-5", "70000", ""):
            with self.subTest(raw=raw), patch.dict(
                os.environ, {"BYTEWOLF_TARGET_CLASSIFIER_MAVSDK_PORT": raw}, clear=False
            ):
                self.assertEqual(
                    default_target_classifier_mavsdk_port(),
                    _DEFAULT_CLASSIFIER_MAVSDK_PORT,
                )


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

    async def test_legacy_autostart_without_gz_or_sih_signals_is_refused(self) -> None:
        drone = _drone_with_identity(
            release_name="RELEASE",
            sys_autostart=2500,
            sim_gz_en=None,
        )
        with self.assertRaises(DemoPointRtlError):
            await require_px4_sitl_indication(drone, timeout_s=1.0)

    async def test_dev_alone_is_insufficient(self) -> None:
        drone = _drone_with_identity(
            release_name="DEV",
            sys_autostart=0,
            sim_gz_en=None,
        )
        with self.assertRaises(DemoPointRtlError) as context:
            await require_px4_sitl_indication(drone, timeout_s=1.0)
        self.assertIn("simulator-specific", str(context.exception))

    async def test_gz_autostart_without_sim_gz_en_is_refused(self) -> None:
        drone = _drone_with_identity(
            product_name="PX4 SITL",
            release_name="RELEASE",
            sys_autostart=4001,
            sim_gz_en=0,
        )
        with self.assertRaises(DemoPointRtlError) as context:
            await require_px4_sitl_indication(drone, timeout_s=1.0)
        self.assertIn("simulator-specific", str(context.exception))

    async def test_identity_markers_without_simulator_param_are_refused(self) -> None:
        drone = _drone_with_identity(
            product_name="PX4 Gazebo SITL",
            release_name="DEV",
            sys_autostart=0,
            sim_gz_en=None,
        )
        with self.assertRaises(DemoPointRtlError):
            await require_px4_sitl_indication(drone, timeout_s=1.0)


class TargetClassifierInternalReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_never_raises_on_identity_timeout(self) -> None:
        drone = _drone_with_identity()
        drone.info.get_product = AsyncMock(side_effect=TimeoutError())

        async def fail_indication(*_args, **_kwargs):
            raise DemoPointRtlError("indicators insufficient")

        with patch(
            "brain.mission.sitl_target.require_px4_sitl_indication",
            AsyncMock(side_effect=fail_indication),
        ):
            result = await TargetClassifier.classify(drone, timeout_s=0.05)

        self.assertEqual(result.verdict, "unknown")
        self.assertTrue(result.reason.strip())

    async def test_classify_maps_runtime_error_to_unknown(self) -> None:
        drone = MagicMock(info=None)

        result = await TargetClassifier.classify(drone, timeout_s=1.0)
        self.assertEqual(result.verdict, "unknown")
        self.assertIn("MAVSDK Info", result.reason)


class TargetClassifierEndpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_at_endpoint_returns_unknown_when_connect_fails(self) -> None:
        system = MagicMock()
        system.connect = AsyncMock(side_effect=RuntimeError("bind failed"))
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=system)  # type: ignore[attr-defined]
        with (
            patch("brain.cli.mavsdk_lifecycle.acquire_px4_link"),
            patch("brain.cli.mavsdk_lifecycle.stop_owned_mavsdk_server"),
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
        ):
            result = await TargetClassifier.classify_at_endpoint(
                "udpin://127.0.0.1:14540",
                connection_timeout_s=1.0,
            )
        self.assertEqual(result.verdict, "unknown")
        self.assertIn("bind failed", result.reason)

    async def test_classify_at_endpoint_times_out_waiting_for_connection_state(self) -> None:
        async def slow_states():
            while True:
                await asyncio.sleep(60)
                yield MagicMock(is_connected=False)

        system = MagicMock()
        system.connect = AsyncMock()
        system.core.connection_state = slow_states
        mavsdk = ModuleType("mavsdk")
        mavsdk.System = MagicMock(return_value=system)  # type: ignore[attr-defined]
        with (
            patch("brain.cli.mavsdk_lifecycle.acquire_px4_link"),
            patch("brain.cli.mavsdk_lifecycle.stop_owned_mavsdk_server"),
            patch.dict(sys.modules, {"mavsdk": mavsdk}),
        ):
            result = await TargetClassifier.classify_at_endpoint(
                "udpin://127.0.0.1:14540",
                connection_timeout_s=0.05,
                classify_timeout_s=0.05,
            )
        self.assertEqual(result.verdict, "unknown")
        self.assertTrue(result.reason.strip())


class TargetClassifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_classify_returns_sitl_verified_for_stock_gazebo(self) -> None:
        result = await TargetClassifier.classify(_drone_with_identity(), timeout_s=1.0)
        self.assertEqual(result.verdict, "sitl_verified")
        self.assertIsNotNone(result.proof)

    async def test_classify_marks_pixhawk_like_as_real_suspected(self) -> None:
        drone = _drone_with_identity(
            product_name="PX4 Pixhawk 6X",
            vendor_name="Holybro",
            sys_autostart=0,
            sim_gz_en=None,
        )
        result = await TargetClassifier.classify(drone, timeout_s=1.0)
        self.assertEqual(result.verdict, "real_suspected")

    async def test_classify_marks_param_failure_as_unknown(self) -> None:
        drone = _drone_with_identity(param_timeout="SYS_AUTOSTART")
        result = await TargetClassifier.classify(drone, timeout_s=0.05)
        self.assertEqual(result.verdict, "unknown")
        self.assertIn("SYS_AUTOSTART", result.reason)


class TelemetryDocumentClassifierTests(unittest.TestCase):
    def test_empty_document_is_unknown(self) -> None:
        self.assertEqual(classify_telemetry_document({}), "unknown")

    def test_verified_sitl_identity_in_snapshot(self) -> None:
        document = {
            "vehicle_identity": {
                "product_name": "PX4 SITL",
                "vendor_name": "undefined",
                "flight_sw_version_type": "RELEASE",
                "sys_autostart": 4001,
                "sim_gz_en": 1,
            }
        }
        self.assertEqual(classify_telemetry_document(document), "sitl_verified")

    def test_pixhawk_like_identity_in_snapshot_is_real_suspected(self) -> None:
        document = {
            "vehicle_identity": {
                "product_name": "PX4 Pixhawk 6X",
                "vendor_name": "Holybro",
                "flight_sw_version_type": "RELEASE",
                "sys_autostart": 0,
            }
        }
        self.assertEqual(classify_telemetry_document(document), "real_suspected")

    def test_never_defaults_to_sitl_verified_without_proof(self) -> None:
        document = {"autopilot": "px4", "source": "pymavlink"}
        self.assertEqual(classify_telemetry_document(document), "unknown")

    def test_top_level_identity_fields_are_ignored_without_vehicle_identity(self) -> None:
        document = {
            "product_name": "PX4 SITL",
            "sys_autostart": 4001,
            "sim_gz_en": 1,
        }
        self.assertEqual(classify_telemetry_document(document), "unknown")

    def test_partial_release_identity_without_sys_autostart_is_unknown(self) -> None:
        document = {
            "vehicle_identity": {
                "product_name": "undefined",
                "vendor_name": "undefined",
                "flight_sw_version_type": "RELEASE",
            }
        }
        self.assertEqual(classify_telemetry_document(document), "unknown")


if __name__ == "__main__":
    unittest.main()
