"""Agent output may omit policy fields because the gateway supplies them."""

import json
import unittest

from apps.gateway.nim_mission_agent import MissionAgentRequest, NIMMissionAgent
from brain.mission_spec.validation import load_mission_safety_profile


class ServerBoundProposalTests(unittest.TestCase):
    def test_intent_and_steps_only_are_bound_to_the_active_platform(self) -> None:
        profile = load_mission_safety_profile("shared/config/x500v2/twin.yaml")
        response = {"choices": [{"message": {"tool_calls": [{"function": {
            "name": "propose_mission_spec",
            "arguments": json.dumps({"kind": "mission_proposal", "mission_spec": {
                "intent": "test_flight",
                "steps": [
                    {"type": "TAKEOFF", "altitude_m": 2.0},
                    {"type": "HOLD", "duration_s": 3.0},
                    {"type": "LAND"},
                ],
            }}),
        }}]}}]}

        result = NIMMissionAgent("key", "model", post_json=lambda *_: response).propose(
            MissionAgentRequest("safe", profile)
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.mission_spec["vehicle_id"], profile.vehicle_id)


if __name__ == "__main__":
    unittest.main()
