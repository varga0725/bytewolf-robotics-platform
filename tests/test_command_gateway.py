"""The gateway must turn words into a validated mission, never into a bypass.

Every accepted result here is one the existing validator and SafetyGate
approved; every rejection names the clause or constraint that caused it. No test
opens a PX4 connection, because the gateway never does.
"""

import asyncio
import unittest

from brain.mission_spec.command_gateway import (
    CommandRequest,
    GatewayResult,
    interpret_command,
)
from brain.mission_spec.orchestrator import (
    MissionSpecExecutionError,
    execute_compiled_mission,
)
from brain.mission_spec.validation import load_mission_safety_profile


_PROFILE = load_mission_safety_profile("shared/config/x500v2/twin.yaml")
_VEHICLE = "x500v2_reference_01"

_CANONICAL_EN = "Take off to 2 metres, fly to the designated point, then come back and land"
_CANONICAL_HU = "Szállj fel 2 méterre, repülj a kijelölt ponthoz, majd gyere vissza és szállj le"


def _interpret(text: str, **kwargs) -> GatewayResult:
    return interpret_command(CommandRequest(text, _VEHICLE, **kwargs), _PROFILE)


class CanonicalDemoTests(unittest.TestCase):
    def test_the_english_demo_yields_a_validated_mission(self) -> None:
        result = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))

        self.assertTrue(result.accepted)
        self.assertEqual([step["type"] for step in result.mission_spec["steps"]], ["TAKEOFF", "GOTO_LOCAL", "RTL"])
        self.assertIsNotNone(result.mission)

    def test_the_hungarian_demo_yields_the_same_mission(self) -> None:
        english = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))
        hungarian = _interpret(_CANONICAL_HU, designated_point_m=(5.0, 0.0))

        self.assertTrue(hungarian.accepted)
        self.assertEqual(
            [s["type"] for s in hungarian.mission_spec["steps"]],
            [s["type"] for s in english.mission_spec["steps"]],
        )

    def test_the_same_request_is_deterministic(self) -> None:
        first = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))
        second = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))

        self.assertEqual(first.mission_spec, second.mission_spec)
        self.assertEqual(first.mission_spec["mission_id"], second.mission_spec["mission_id"])

    def test_the_generated_spec_uses_the_platform_constraints(self) -> None:
        result = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))

        constraints = result.mission_spec["constraints"]
        self.assertEqual(constraints["max_altitude_m"], _PROFILE.max_altitude_m)
        self.assertEqual(constraints["max_speed_m_s"], _PROFILE.max_speed_m_s)
        self.assertEqual(constraints["max_radius_m"], _PROFILE.max_radius_m)


class SupportedIntentTests(unittest.TestCase):
    def test_takeoff_hover_land(self) -> None:
        result = _interpret("Take off to 2 m, hover for 5 seconds, then land")

        self.assertTrue(result.accepted)
        self.assertEqual(
            [s["type"] for s in result.mission_spec["steps"]], ["TAKEOFF", "HOLD", "LAND"]
        )

    def test_go_north_and_land(self) -> None:
        result = _interpret("Take off to 3 m, fly 5 m north, then land")

        self.assertTrue(result.accepted)
        goto = next(s for s in result.mission_spec["steps"] if s["type"] == "GOTO_LOCAL")
        self.assertEqual((goto["north_m"], goto["east_m"]), (5.0, 0.0))
        # The goto flies at the altitude the takeoff reached.
        self.assertEqual(goto["down_m"], -3.0)

    def test_return_to_home(self) -> None:
        result = _interpret("Take off to 2 m and return home")

        self.assertTrue(result.accepted)
        self.assertEqual(result.mission.terminal_action, "RTL")

    def test_a_land_after_return_is_absorbed_not_a_second_terminal(self) -> None:
        result = _interpret("Take off to 2 m, come back and land")

        self.assertTrue(result.accepted)
        self.assertEqual([s["type"] for s in result.mission_spec["steps"]], ["TAKEOFF", "RTL"])


class RejectionTests(unittest.TestCase):
    def test_an_over_altitude_takeoff_is_rejected_by_the_safety_layer(self) -> None:
        """Parsing succeeds; the platform ceiling is what refuses it."""
        result = _interpret("Take off to 500 metres then land")

        self.assertFalse(result.accepted)
        self.assertIsNone(result.mission)
        rejection = result.rejections[0]
        self.assertIn("altitude", rejection.reason.lower())
        self.assertEqual(rejection.source_text, "Take off to 500 metres then land")

    def test_an_out_of_range_waypoint_is_rejected(self) -> None:
        result = _interpret("Take off to 2 m, fly 500 m north, then land")

        self.assertFalse(result.accepted)
        self.assertTrue(result.rejections)

    def test_an_unsupported_instruction_names_the_offending_clause(self) -> None:
        result = _interpret("Take off to 2 m, do a backflip, then land")

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejections[0].source_text, "do a backflip")
        self.assertIn("does not support", result.rejections[0].reason)

    def test_a_designated_point_without_a_coordinate_is_refused_as_ambiguous(self) -> None:
        result = _interpret("Take off to 2 m, fly to the designated point, then land")

        self.assertFalse(result.accepted)
        self.assertEqual(result.rejections[0].constraint, "designated_point")

    def test_an_empty_request_is_refused(self) -> None:
        result = _interpret("   ")

        self.assertFalse(result.accepted)
        self.assertIn("empty", result.rejections[0].reason.lower())

    def test_two_terminals_are_refused_by_the_contract(self) -> None:
        """Land then return is contradictory; the validator, not the parser, catches it."""
        result = _interpret("Take off to 2 m, land, then return home")

        self.assertFalse(result.accepted)
        self.assertTrue(any("terminal" in rejection.reason.lower() for rejection in result.rejections))


class SafetyBoundaryTests(unittest.TestCase):
    def test_the_gateway_imports_no_flight_or_mavsdk_path(self) -> None:
        """The front door cannot emit MAVLink because it never imports a way to."""
        import ast
        from pathlib import Path

        import brain.mission_spec.command_gateway as gateway

        tree = ast.parse(Path(gateway.__file__).read_text(encoding="utf-8"))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)

        for module in imported:
            self.assertNotIn("mavsdk", module, "the gateway must not import MAVSDK")
            self.assertNotIn("adapters", module, "the gateway must not import a flight adapter")

    def test_a_rejected_request_yields_no_mission_to_execute(self) -> None:
        result = _interpret("Take off to 500 m then land")

        self.assertIsNone(result.mission)


class _RecordingAdapter:
    """A flight adapter that records the route taken instead of touching PX4."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, mission: object) -> str:
        self.calls.append("takeoff-hover-land")
        return self.calls[-1]

    async def execute_waypoint_mission(self, mission: object) -> str:
        self.calls.append("waypoint-land")
        return self.calls[-1]

    async def execute_return_to_home_mission(self, mission: object) -> str:
        self.calls.append("return-to-home")
        return self.calls[-1]


class OrchestrationOrderingTests(unittest.TestCase):
    """gateway -> validator -> compiler -> orchestrator, with no PX4 in the loop."""

    def _route(self, text: str) -> tuple[GatewayResult, list[str]]:
        result = _interpret(text)
        adapter = _RecordingAdapter()
        if result.accepted:
            asyncio.run(execute_compiled_mission(adapter, result.mission))
        return result, adapter.calls

    def test_a_hover_land_request_routes_to_the_hover_land_adapter(self) -> None:
        result, calls = self._route("Take off to 2 m, hover for 3 seconds, then land")

        self.assertTrue(result.accepted)
        self.assertEqual(calls, ["takeoff-hover-land"])

    def test_a_waypoint_request_routes_to_the_waypoint_adapter(self) -> None:
        result, calls = self._route("Take off to 2 m, fly 5 m north, hover for 3 seconds, then land")

        self.assertEqual(calls, ["waypoint-land"])

    def test_a_return_request_routes_to_the_return_adapter(self) -> None:
        result, calls = self._route("Take off to 2 m, hover for 3 seconds, then return home")

        self.assertEqual(calls, ["return-to-home"])

    def test_a_rejected_request_never_reaches_the_orchestrator(self) -> None:
        result, calls = self._route("Take off to 500 m then land")

        self.assertFalse(result.accepted)
        self.assertEqual(calls, [])

    def test_the_canonical_demo_compiles_but_its_shape_has_no_adapter_path(self) -> None:
        """A known limitation: takeoff-goto-return with no hover is valid but unroutable.

        The gateway and validator approve it; the orchestrator's bounded adapters
        cannot represent that exact shape, and it refuses rather than dropping a
        step. Surfaced here so the gap is visible, not silently accepted.
        """
        result = _interpret(_CANONICAL_EN, designated_point_m=(5.0, 0.0))
        self.assertTrue(result.accepted)

        with self.assertRaises(MissionSpecExecutionError):
            asyncio.run(execute_compiled_mission(_RecordingAdapter(), result.mission))


if __name__ == "__main__":
    unittest.main()
