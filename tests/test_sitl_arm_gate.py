"""Contract tests for the pre-arm SITL identity gate on sim runners."""

from __future__ import annotations

import importlib
import inspect
import unittest


def _sitl_gate_precedes_arm(module_name: str, function_name: str) -> None:
    module = importlib.import_module(module_name)
    source = inspect.getsource(getattr(module, function_name))
    arm_index = source.index("await drone.action.arm()")
    gate_index = source.rindex("require_px4_sitl_indication", 0, arm_index)
    if gate_index >= arm_index:
        raise AssertionError(f"{module_name}.{function_name}: SITL gate must appear before arm()")


class SimRunnerSitlGateTests(unittest.TestCase):
    def test_avoidance_route_run_gates_before_arm(self) -> None:
        _sitl_gate_precedes_arm("simulation.control.avoidance_route_run", "_fly_avoidance_route")

    def test_mission_route_run_gates_before_arm(self) -> None:
        _sitl_gate_precedes_arm("simulation.control.mission_route_run", "_fly_route")

    def test_shield_run_gates_before_arm(self) -> None:
        _sitl_gate_precedes_arm("simulation.control.shield_run", "_fly")

    def test_offboard_watchdog_run_gates_before_arm(self) -> None:
        _sitl_gate_precedes_arm("simulation.control.offboard_watchdog_run", "_fly")


if __name__ == "__main__":
    unittest.main()