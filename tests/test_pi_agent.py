"""The dashboard's Pi boundary is typed and never grants flight control."""

from __future__ import annotations

import subprocess
import unittest
from unittest import mock

from apps.gateway.pi_agent import PiAgentClient, PiAgentError, _run_pi


class PiAgentClientTests(unittest.TestCase):
    def test_forwards_a_session_bound_conversation_to_the_runner(self) -> None:
        requests: list[dict[str, object]] = []
        client = PiAgentClient(
            runner=lambda request: requests.append(request) or {
                "text": "Szia! Mire vagy kíváncsi?",
                "requests_drone_action": False,
            }
        )

        reply = client.converse("browser-session", "csáó")

        self.assertEqual(reply.text, "Szia! Mire vagy kíváncsi?")
        self.assertFalse(reply.requests_drone_action)
        self.assertEqual(requests, [{"session_id": "browser-session", "text": "csáó"}])

    def test_rejects_a_malformed_runner_response_without_falling_back_to_flight(self) -> None:
        client = PiAgentClient(runner=lambda _request: {"text": "hiányzik a jelző"})

        with self.assertRaisesRegex(PiAgentError, "invalid"):
            client.converse("browser-session", "repülj")

    def test_rejects_an_oversized_agent_message(self) -> None:
        client = PiAgentClient(
            runner=lambda _request: {"text": "x" * 2_001, "requests_drone_action": False}
        )

        with self.assertRaisesRegex(PiAgentError, "invalid"):
            client.converse("browser-session", "szia")


class RunnerDiagnosticTests(unittest.TestCase):
    """A failed runner must leave its cause somewhere the operator can read.

    The runner writes one safe line to stderr for exactly this purpose. Nothing
    read it, so every failure looked identical from the dashboard and the reason
    was gone for good.
    """

    def _run(self, completed: subprocess.CompletedProcess[str]) -> list[str]:
        with mock.patch("apps.gateway.pi_agent.shutil.which", return_value="/usr/bin/node"), \
             mock.patch("apps.gateway.pi_agent._RUNNER_PATH") as runner_path, \
             mock.patch("apps.gateway.pi_agent.subprocess.run", return_value=completed), \
             self.assertLogs("apps.gateway.pi_agent", level="WARNING") as logs:
            runner_path.is_file.return_value = True
            with self.assertRaises(PiAgentError):
                _run_pi({"session_id": "s", "text": "szia"})
        return logs.output

    def test_a_non_zero_exit_records_the_runners_own_cause(self) -> None:
        output = self._run(
            subprocess.CompletedProcess([], 1, stdout="", stderr="Pi runner failed: upstream refused.")
        )

        self.assertIn("upstream refused", output[0])
        self.assertIn("status 1", output[0])

    def test_a_silent_failure_is_recorded_as_silent_rather_than_dropped(self) -> None:
        output = self._run(subprocess.CompletedProcess([], 2, stdout="", stderr=""))

        self.assertIn("no diagnostic output", output[0])

    def test_unparsable_output_is_recorded_too(self) -> None:
        output = self._run(subprocess.CompletedProcess([], 0, stdout="nem JSON", stderr="warn: half a turn"))

        self.assertIn("half a turn", output[0])

    def test_the_recorded_cause_is_bounded(self) -> None:
        output = self._run(subprocess.CompletedProcess([], 1, stdout="", stderr="x" * 5_000))

        self.assertLess(len(output[0]), 2_500)


if __name__ == "__main__":
    unittest.main()
