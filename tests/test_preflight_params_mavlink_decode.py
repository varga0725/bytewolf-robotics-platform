"""PX4 PARAM_VALUE bit-cast decoding."""

from __future__ import annotations

import unittest

from brain.preflight_params.evaluate import evaluate_params
from brain.preflight_params.mavlink import decode_param_message
from brain.preflight_params.param_value import (
    MAV_PARAM_TYPE_INT32,
    MAV_PARAM_TYPE_REAL32,
    ParamValue,
    encode_px4_int32_wire_value,
    encode_px4_real32_wire_value,
)
from brain.preflight_params.rules import RULES


_GZ_X500_GROUND_TRUTH: dict[str, tuple[int | float, int]] = {
    "NAV_DLL_ACT": (2, MAV_PARAM_TYPE_INT32),
    "COM_DL_LOSS_T": (10, MAV_PARAM_TYPE_INT32),
    "COM_RC_IN_MODE": (1, MAV_PARAM_TYPE_INT32),
    "COM_LOW_BAT_ACT": (3, MAV_PARAM_TYPE_INT32),
    "GF_ACTION": (2, MAV_PARAM_TYPE_INT32),
    "GF_MAX_HOR_DIST": (0.0, MAV_PARAM_TYPE_REAL32),
    "GF_MAX_VER_DIST": (0.0, MAV_PARAM_TYPE_REAL32),
    "COM_ARM_WO_GPS": (1, MAV_PARAM_TYPE_INT32),
    "NAV_RCL_ACT": (2, MAV_PARAM_TYPE_INT32),
    "COM_RC_LOSS_T": (0.5, MAV_PARAM_TYPE_REAL32),
}


class MavlinkParamDecodeTests(unittest.TestCase):
    def test_int32_params_decode_from_px4_bitcast_wire_values(self) -> None:
        for name in (
            "NAV_DLL_ACT",
            "COM_DL_LOSS_T",
            "COM_RC_IN_MODE",
            "COM_LOW_BAT_ACT",
            "GF_ACTION",
            "COM_ARM_WO_GPS",
            "NAV_RCL_ACT",
        ):
            expected, param_type = _GZ_X500_GROUND_TRUTH[name]
            wire = encode_px4_int32_wire_value(int(expected))
            self.assertNotEqual(wire, float(expected), name)
            decoded = decode_param_message(wire, param_type)
            self.assertTrue(decoded.is_integer)
            self.assertEqual(decoded.display_value, expected)
            self.assertEqual(int(decoded.value), expected)

    def test_real32_param_decodes_unchanged(self) -> None:
        wire = encode_px4_real32_wire_value(0.5)
        decoded = decode_param_message(wire, MAV_PARAM_TYPE_REAL32)
        self.assertFalse(decoded.is_integer)
        self.assertAlmostEqual(float(decoded.value), 0.5)

    def test_misread_int32_as_float_before_fix_would_fail_nav_dll_act(self) -> None:
        wire = encode_px4_int32_wire_value(2)
        wrong = float(wire)
        self.assertLess(abs(wrong), 1e-30)
        decoded = decode_param_message(wire, MAV_PARAM_TYPE_INT32)
        self.assertEqual(decoded.display_value, 2)

    def test_gz_x500_fixture_summary_after_decode(self) -> None:
        params: dict[str, ParamValue] = {}
        for name, (expected, param_type) in _GZ_X500_GROUND_TRUTH.items():
            if param_type == MAV_PARAM_TYPE_INT32:
                wire = encode_px4_int32_wire_value(int(expected))
            else:
                wire = encode_px4_real32_wire_value(float(expected))
            params[name] = decode_param_message(wire, param_type)
        report = evaluate_params(params)
        self.assertEqual(report.summary, "FAIL (2 failed, 3 warn, 5 pass)")
        self.assertEqual(len(report.results), len(RULES))
        nav_dll = next(r for r in report.results if r.param == "NAV_DLL_ACT")
        self.assertEqual(nav_dll.status, "PASS")
        self.assertEqual(nav_dll.actual, 2)


if __name__ == "__main__":
    unittest.main()