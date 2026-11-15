"""vehicle_identity block shape and passive param mapping for target_class."""

from __future__ import annotations

import unittest

from brain.mission.sitl_target import classify_telemetry_document
from brain.telemetry.autopilot_identity import AutopilotIdentityCollector
from brain.telemetry.vehicle_identity import vehicle_identity_from_params


class VehicleIdentityBlockTests(unittest.TestCase):
    def test_gz_x500_maps_to_sitl_verified(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=4001, sim_gz_en=1)
        identity["mavlink_sysid"] = 1
        identity["mavlink_compid"] = 1
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "sitl_verified")

    def test_stale_identity_is_unknown(self) -> None:
        document = {"vehicle_identity": {"stale": True, "mavlink_sysid": 1, "mavlink_compid": 1}}
        self.assertEqual(classify_telemetry_document(document), "unknown")

    def test_sih_autostart_with_vehicle_type_maps_to_sitl_verified(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=10040, sih_vehicle_type=2)
        assert identity is not None
        identity["mavlink_sysid"] = 1
        identity["mavlink_compid"] = 1
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "sitl_verified")

    def test_missing_sys_autostart_is_unknown(self) -> None:
        self.assertIsNone(vehicle_identity_from_params(sys_autostart=None))
        self.assertEqual(classify_telemetry_document({}), "unknown")

    def test_gz_without_sim_gz_en_is_unknown(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=4001)
        document = {"vehicle_identity": identity or {}}
        self.assertEqual(classify_telemetry_document(document), "unknown")


class _ParamValue:
    def __init__(self, name: str, value: int, *, sysid: int = 1, compid: int = 1) -> None:
        self.param_id = name
        from brain.preflight_params.param_value import encode_px4_int32_wire_value

        self.param_value = encode_px4_int32_wire_value(value)
        self.param_type = 6
        self._sysid = sysid
        self._compid = compid

    def get_srcSystem(self) -> int:
        return self._sysid

    def get_srcComponent(self) -> int:
        return self._compid


class AutopilotIdentityCollectorTests(unittest.TestCase):
    def test_ignores_param_value_from_wrong_source(self) -> None:
        collector = AutopilotIdentityCollector()
        collector.observe_autopilot_heartbeat(1, 1, monotonic=0.0)
        collector.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001, sysid=2, compid=1))
        block = collector.identity_block()
        assert block is not None
        self.assertTrue(block.get("stale"))

    def test_sysid_change_clears_cached_identity(self) -> None:
        collector = AutopilotIdentityCollector()
        collector.observe_autopilot_heartbeat(1, 1, monotonic=0.0)
        collector.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001))
        collector.apply_param_value(_ParamValue("SIM_GZ_EN", 1))
        block = collector.identity_block()
        assert block is not None
        self.assertNotIn("stale", block)
        self.assertEqual(block.get("sys_autostart"), 4001)

        collector.observe_autopilot_heartbeat(2, 1, monotonic=1.0)
        block = collector.identity_block()
        assert block is not None
        self.assertTrue(block.get("stale"))
        self.assertNotIn("sys_autostart", block)

    def test_link_loss_marks_identity_stale(self) -> None:
        collector = AutopilotIdentityCollector(link_loss_timeout_s=1.0)
        collector.observe_autopilot_heartbeat(1, 1, monotonic=0.0)
        collector.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001))
        collector.apply_param_value(_ParamValue("SIM_GZ_EN", 1))
        self.assertNotIn("stale", collector.identity_block() or {})  # type: ignore[arg-type]
        self.assertTrue(collector.observe_link_quiet(monotonic=2.0))
        block = collector.identity_block()
        assert block is not None
        self.assertTrue(block.get("stale"))
        self.assertNotIn("sys_autostart", block)

    def test_reconnect_after_loss_forces_reread(self) -> None:
        collector = AutopilotIdentityCollector(link_loss_timeout_s=1.0)
        collector.observe_autopilot_heartbeat(1, 1, monotonic=0.0)
        collector.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001))
        collector.apply_param_value(_ParamValue("SIM_GZ_EN", 1))
        collector.observe_link_quiet(monotonic=2.0)
        collector.observe_autopilot_heartbeat(1, 1, monotonic=2.5)
        block = collector.identity_block()
        assert block is not None
        self.assertTrue(block.get("stale"))
        self.assertEqual(collector.pop_follow_up_requests(), ("SYS_AUTOSTART",))

    def test_system_time_rewind_clears_identity(self) -> None:
        collector = AutopilotIdentityCollector()
        collector.observe_autopilot_heartbeat(1, 1, monotonic=0.0)
        collector.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001))
        collector.apply_param_value(_ParamValue("SIM_GZ_EN", 1))
        collector.observe_system_time(50_000)
        self.assertTrue(collector.observe_system_time(1_000))
        block = collector.identity_block()
        assert block is not None
        self.assertTrue(block.get("stale"))


if __name__ == "__main__":
    unittest.main()