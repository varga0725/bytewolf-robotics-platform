"""Telegram must stay outside the deterministic flight-control boundary."""

import unittest

from apps.gateway.telegram_mission_gateway import _converse_with_nim, ConversationReply, TelegramMissionGateway


class TelegramMissionGatewayTests(unittest.TestCase):
    def setUp(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.reviewed: list[str] = []
        self.executed: list[str] = []
        self.gateway = TelegramMissionGateway(
            allowed_chat_ids=frozenset({42}),
            send_message=lambda chat_id, text: self.sent.append((chat_id, text)),
            review_mission=lambda command: self.reviewed.append(command) or "a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json",
            execute_plan=lambda plan: self.executed.append(plan) or "started",
            converse=lambda _text: ConversationReply("Rendben, elkészítem a biztonságos tervet.", True),
        )

    def test_ignores_an_unauthorized_chat_without_replying(self) -> None:
        self.gateway.handle_update(_update(99, "/mission take off"))

        self.assertEqual(self.reviewed, [])
        self.assertEqual(self.sent, [])

    def test_plain_language_message_creates_a_review_only(self) -> None:
        self.gateway.handle_update(_update(42, "Szállj fel 2 méterre, majd szállj le."))

        self.assertEqual(self.reviewed, ["Szállj fel 2 méterre, majd szállj le."])
        self.assertEqual(self.executed, [])
        self.assertIn("Indítsam a szimulációban?", self.sent[0][1])

    def test_natural_confirmation_starts_only_the_pending_plan(self) -> None:
        self.gateway.handle_update(_update(42, "Szállj fel 2 méterre, majd szállj le."))
        self.gateway.handle_update(_update(42, "igen"))

        self.assertEqual(self.executed, ["a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json"])

    def test_execute_only_accepts_a_reviewed_plan_basename(self) -> None:
        self.gateway.handle_update(_update(42, "/execute ../../anything.json"))

        self.assertEqual(self.executed, [])
        self.assertIn("reviewed plan filename", self.sent[0][1])

    def test_execute_runs_the_named_reviewed_plan_not_a_new_prompt(self) -> None:
        self.gateway.handle_update(_update(42, "/execute a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json"))

        self.assertEqual(self.executed, ["a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json"])
        self.assertEqual(self.reviewed, [])
        self.assertEqual(self.sent, [(42, "SITL execution started: a3b9c777-4860-4b6d-bf59-1a4a98c31ea3.mission-spec.json")])

    def test_help_does_not_call_nim_or_px4(self) -> None:
        self.gateway.handle_update(_update(42, "/help"))

        self.assertEqual(self.reviewed, [])
        self.assertEqual(self.executed, [])
        self.assertIn("Beszélj hozzám természetesen", self.sent[0][1])

    def test_ignores_a_group_message_even_when_the_group_id_is_allowlisted(self) -> None:
        self.gateway.handle_update(_update(42, "/mission take off", chat_type="group", sender_id=7))

        self.assertEqual(self.reviewed, [])
        self.assertEqual(self.sent, [])

    def test_clear_vertical_request_bypasses_ambiguous_conversation_routing(self) -> None:
        reply = _converse_with_nim("emelkedj fel 2 méterre")

        self.assertTrue(reply.requests_drone_action)
        self.assertIn("biztonságos tervet", reply.text)


def _update(
    chat_id: int, text: str, *, chat_type: str = "private", sender_id: int | None = None
) -> dict[str, object]:
    return {
        "message": {
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": chat_id if sender_id is None else sender_id},
            "text": text,
        }
    }


if __name__ == "__main__":
    unittest.main()
