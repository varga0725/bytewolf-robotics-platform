"""vehicle_identity block shape and passive param mapping for target_class."""

from __future__ import annotations

import unittest

from brain.mission.sitl_target import classify_telemetry_document
from brain.telemetry.pymavlink_passive_params import PassiveVehicleIdentityTracker
from brain.telemetry.vehicle_identity import vehicle_identity_from_params


class VehicleIdentityBlockTests(unittest.TestCase):
    def test_gz_x500_maps_to_sitl_verified(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=4001, sim_gz_en=1)
        self.assertIsNotNone(identity)
        assert identity is not None
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "sitl_verified")

    def test_sih_autostart_with_vehicle_type_maps_to_sitl_verified(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=10040, sih_vehicle_type=2)
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "sitl_verified")

    def test_missing_sys_autostart_is_unknown(self) -> None:
        self.assertIsNone(vehicle_identity_from_params(sys_autostart=None))
        self.assertEqual(classify_telemetry_document({}), "unknown")

    def test_gz_without_sim_gz_en_is_unknown(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=4001)
        document = {"vehicle_identity": identity or {}}
        self.assertEqual(classify_telemetry_document(document), "unknown")

    def test_sim_gz_en_zero_is_unknown(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=4001, sim_gz_en=0)
        assert identity is not None
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "unknown")

    def test_out_of_range_autostart_is_unknown(self) -> None:
        identity = vehicle_identity_from_params(sys_autostart=1200, sim_gz_en=1)
        assert identity is not None
        document = {"vehicle_identity": identity}
        self.assertEqual(classify_telemetry_document(document), "unknown")


class _ParamValue:
    def __init__(self, name: str, value: int) -> None:
        self.param_id = name
        from brain.preflight_params.param_value import encode_px4_int32_wire_value

        self.param_value = encode_px4_int32_wire_value(value)
        self.param_type = 6  # INT32


class PassiveVehicleIdentityTrackerTests(unittest.TestCase):
    def test_sysid_change_clears_cached_identity(self) -> None:
        tracker = PassiveVehicleIdentityTracker()
        tracker.on_vehicle_sysid(1, monotonic=0.0)
        tracker.apply_param_value(_ParamValue("SYS_AUTOSTART", 4001))
        tracker.apply_param_value(_ParamValue("SIM_GZ_EN", 1))
        self.assertEqual(tracker.identity_block(), {"sys_autostart": 4001, "sim_gz_en": 1})

        tracker.on_vehicle_sysid(2, monotonic=1.0)
        self.assertIsNone(tracker.identity_block())
        self.assertIn("SYS_AUTOSTART", tracker.pop_follow_up_requests())

    def test_follow_up_requests_after_autostart(self) -> None:
        tracker = PassiveVehicleIdentityTracker()
        tracker.on_vehicle_sysid(1, monotonic=0.0)
        tracker.apply_param_value(_ParamValue("SYS_AUTOSTART", 10045))
        pending = tracker.pop_follow_up_requests()
        self.assertEqual(pending, ("SIH_VEHICLE_TYPE",))


if __name__ == "__main__":
    unittest.main()