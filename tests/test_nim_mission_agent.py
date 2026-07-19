"""NIM mission agent proposals must remain behind the deterministic safety boundary."""

import json
import unittest
from uuid import uuid4

from apps.gateway.nim_mission_agent import (
    MissionAgentRequest,
    NIMMissionAgent,
    NIMMissionAgentError,
)
from brain.mission_spec.validation import load_mission_safety_profile


PROFILE = load_mission_safety_profile("shared/config/x500v2/twin.yaml")


def _spec(*steps: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "0.1",
        "mission_id": str(uuid4()),
        "vehicle_id": PROFILE.vehicle_id,
        "intent": "test_flight",
        "constraints": {
            "max_altitude_m": PROFILE.max_altitude_m,
            "max_speed_m_s": PROFILE.max_speed_m_s,
            "max_radius_m": PROFILE.max_radius_m,
            "minimum_battery_percent_to_start": PROFILE.minimum_battery_percent_to_start,
            "loss_of_link_action": PROFILE.loss_of_link_action,
        },
        "steps": list(steps),
        "abort_policy": {"on_timeout": "LAND", "on_low_battery": "RTL", "on_position_invalid": "LAND"},
    }


def _agent_response(payload: object) -> dict[str, object]:
    return {"choices": [{"message": {"content": json.dumps(payload)}}]}


class NIMMissionAgentTests(unittest.TestCase):
    def test_valid_nim_proposal_is_compiled_without_any_flight_adapter(self) -> None:
        posted: list[dict[str, object]] = []

        def post(url: str, headers: dict[str, str], payload: dict[str, object], timeout_s: float) -> object:
            posted.append(payload)
            return _agent_response(
                {
                    "kind": "mission_proposal",
                    "mission_spec": _spec(
                        {"type": "TAKEOFF", "altitude_m": 2.0},
                        {"type": "HOLD", "duration_s": 3.0},
                        {"type": "LAND"},
                    ),
                }
            )

        result = NIMMissionAgent("key", "model", post_json=post).propose(
            MissionAgentRequest("Take off two metres, hover three seconds, then land", PROFILE)
        )

        self.assertTrue(result.accepted)
        self.assertIsNotNone(result.mission)
        self.assertEqual(result.model, "model")
        self.assertEqual(posted[0]["model"], "model")
        self.assertIn("tools", posted[0])
        system = posted[0]["messages"][0]["content"]
        self.assertIn("down_m = -altitude_m", system)
        self.assertIn("előre", system)

    def test_invalid_model_json_is_refused(self) -> None:
        agent = NIMMissionAgent("key", "model", post_json=lambda *_: {"choices": [{"message": {"content": "nope"}}]})

        result = agent.propose(MissionAgentRequest("fly", PROFILE))

        self.assertFalse(result.accepted)
        self.assertIn("JSON", result.rejections[0].reason)

    def test_json_wrapped_in_a_markdown_fence_is_parsed_then_validated(self) -> None:
        proposal = {"kind": "mission_proposal", "mission_spec": _spec(
            {"type": "TAKEOFF", "altitude_m": 2.0},
            {"type": "HOLD", "duration_s": 3.0},
            {"type": "LAND"},
        )}
        agent = NIMMissionAgent(
            "key",
            "model",
            post_json=lambda *_: {"choices": [{"message": {"content": f"```json\n{json.dumps(proposal)}\n```"}}]},
        )

        result = agent.propose(MissionAgentRequest("safe", PROFILE))

        self.assertTrue(result.accepted)

    def test_tool_call_arguments_are_parsed_then_validated(self) -> None:
        proposal = {"kind": "mission_proposal", "mission_spec": _spec(
            {"type": "TAKEOFF", "altitude_m": 2.0},
            {"type": "HOLD", "duration_s": 3.0},
            {"type": "LAND"},
        )}
        response = {"choices": [{"message": {"tool_calls": [{"function": {
            "name": "propose_mission_spec", "arguments": json.dumps(proposal)
        }}]}}]}
        result = NIMMissionAgent("key", "model", post_json=lambda *_: response).propose(
            MissionAgentRequest("safe", PROFILE)
        )

        self.assertTrue(result.accepted)

    def test_unsafe_model_proposal_is_refused_by_deterministic_validator(self) -> None:
        agent = NIMMissionAgent(
            "key",
            "model",
            post_json=lambda *_: _agent_response(
                {
                    "kind": "mission_proposal",
                    "mission_spec": _spec(
                        {"type": "TAKEOFF", "altitude_m": 500.0},
                        {"type": "HOLD", "duration_s": 2.0},
                        {"type": "LAND"},
                    ),
                }
            ),
        )

        result = agent.propose(MissionAgentRequest("go high", PROFILE))

        self.assertFalse(result.accepted)
        self.assertIsNone(result.mission)
        self.assertTrue(any("altitude" in item.reason.lower() for item in result.rejections))

    def test_valid_but_unroutable_shape_is_refused_before_execution(self) -> None:
        agent = NIMMissionAgent(
            "key",
            "model",
            post_json=lambda *_: _agent_response(
                {
                    "kind": "mission_proposal",
                    "mission_spec": _spec(
                        {"type": "TAKEOFF", "altitude_m": 2.0},
                        {"type": "GOTO_LOCAL", "north_m": 5.0, "east_m": 0.0, "down_m": -2.0},
                        {"type": "RTL"},
                    ),
                }
            ),
        )

        result = agent.propose(MissionAgentRequest("go then return", PROFILE))

        self.assertFalse(result.accepted)
        self.assertIn("executable", result.rejections[0].constraint or "")

    def test_missing_credentials_fails_before_network_call(self) -> None:
        with self.assertRaisesRegex(NIMMissionAgentError, "NVIDIA_API_KEY"):
            NIMMissionAgent.from_environment({})

    def test_refuses_an_insecure_nim_endpoint(self) -> None:
        with self.assertRaisesRegex(NIMMissionAgentError, "https"):
            NIMMissionAgent("key", "model", base_url="http://example.test/v1")

    def test_platform_policy_is_owned_by_the_gateway_not_the_model(self) -> None:
        proposal = _spec(
            {"type": "TAKEOFF", "altitude_m": 2.0},
            {"type": "HOLD", "duration_s": 3.0},
            {"type": "LAND"},
        )
        proposal["vehicle_id"] = "attacker"
        proposal["constraints"] = {"max_altitude_m": 999.0}
        proposal["abort_policy"] = {"on_timeout": "RTL"}
        agent = NIMMissionAgent(
            "key", "model", post_json=lambda *_: _agent_response({"kind": "mission_proposal", "mission_spec": proposal})
        )

        result = agent.propose(MissionAgentRequest("safe", PROFILE))

        self.assertTrue(result.accepted)
        self.assertEqual(result.mission_spec["vehicle_id"], PROFILE.vehicle_id)
        self.assertEqual(result.mission_spec["constraints"]["max_altitude_m"], PROFILE.max_altitude_m)


if __name__ == "__main__":
    unittest.main()
