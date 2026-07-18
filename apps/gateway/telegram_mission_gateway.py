"""Telegram adapter for reviewed NIM missions, never direct flight control.

The bot accepts a natural-language request, asks the existing CLI to create a
reviewed MissionSpec, then requires a second `/execute <plan>` message.  The
CLI remains the sole owner of NIM validation, plan hashes, MAVSDK, PX4 and the
safety gates.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from apps.gateway.nim_mission_agent import DEFAULT_NIM_BASE_URL, NIMMissionAgent


_TELEGRAM_API = "https://api.telegram.org"
_MAX_MESSAGE_CHARS = 2_000
_PLAN_NAME = re.compile(r"^[0-9a-f-]{36}\.mission-spec\.json$")
_DEFAULT_PLAN_DIRECTORY = Path("simulation/artifacts/agent-missions")
_execution_lock = Lock()
_active_execution: subprocess.Popen[bytes] | None = None

SendMessage = Callable[[int, str], None]
ReviewMission = Callable[[str], str]
ExecutePlan = Callable[[str], str]


@dataclass(frozen=True)
class ConversationReply:
    """One human-facing response, optionally asking the safety path for a plan."""

    text: str
    requests_drone_action: bool = False


Converse = Callable[[str], ConversationReply]


class TelegramGatewayError(RuntimeError):
    """A Telegram configuration or API failure that reveals no credentials."""


@dataclass(frozen=True)
class TelegramBotConfiguration:
    """Local-only configuration; only explicitly allowlisted chats may act."""

    token: str
    allowed_chat_ids: frozenset[int]

    @classmethod
    def from_environment(cls, environment: Mapping[str, str] | None = None) -> TelegramBotConfiguration:
        values = os.environ if environment is None else environment
        token = values.get("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise TelegramGatewayError("TELEGRAM_BOT_TOKEN must be configured.")
        allowed_chat_ids = _parse_allowed_chat_ids(values.get("TELEGRAM_ALLOWED_CHAT_IDS", ""))
        if not allowed_chat_ids:
            raise TelegramGatewayError("TELEGRAM_ALLOWED_CHAT_IDS must contain at least one numeric chat ID.")
        return cls(token, allowed_chat_ids)


class TelegramMissionGateway:
    """Translate authorized Telegram messages into a two-step SITL workflow."""

    def __init__(
        self,
        *,
        allowed_chat_ids: frozenset[int],
        send_message: SendMessage,
        review_mission: ReviewMission,
        execute_plan: ExecutePlan,
        converse: Converse | None = None,
    ) -> None:
        self._allowed_chat_ids = allowed_chat_ids
        self._send_message = send_message
        self._review_mission = review_mission
        self._execute_plan = execute_plan
        self._converse = converse or (lambda text: ConversationReply(f"Értem: {text}"))
        self._pending_plans: dict[int, str] = {}

    def handle_update(self, update: Mapping[str, object]) -> None:
        message = update.get("message")
        if not isinstance(message, Mapping):
            return
        chat = message.get("chat")
        sender = message.get("from")
        text = message.get("text")
        if (
            not isinstance(chat, Mapping)
            or not isinstance(sender, Mapping)
            or chat.get("type") != "private"
            or not isinstance(chat.get("id"), int)
            or chat.get("id") != sender.get("id")
            or not isinstance(text, str)
        ):
            return
        chat_id = chat["id"]
        if chat_id not in self._allowed_chat_ids:
            return
        command = text.strip()
        if not command:
            self._send_message(chat_id, _help_text())
            return
        if command in {"/help", "/start"}:
            self._send_message(chat_id, _help_text())
            return
        if command.startswith("/execute"):
            self._handle_execute(chat_id, command)
            return
        if command.lower() in {"igen", "indítsd", "indítsd el", "mehet"} and chat_id in self._pending_plans:
            self._start_pending_plan(chat_id)
            return
        if command.lower() in {"nem", "mégse", "ne indítsd"} and chat_id in self._pending_plans:
            self._pending_plans.pop(chat_id)
            self._send_message(chat_id, "Rendben, nem indítok küldetést. Miben segíthetek még?")
            return
        prompt = command.removeprefix("/mission").strip() if command.startswith("/mission") else command
        if not prompt:
            self._send_message(chat_id, "Add a mission after /mission, for example: /mission szállj fel 2 méterre, majd szállj le.")
            return
        if len(prompt) > _MAX_MESSAGE_CHARS:
            self._send_message(chat_id, f"The mission request is too long (maximum {_MAX_MESSAGE_CHARS} characters).")
            return
        try:
            reply = self._converse(prompt)
        except Exception:
            self._send_message(chat_id, "Most nem tudok válaszolni, de a drónhoz sem nyúltam.")
            return
        if not reply.requests_drone_action:
            self._send_message(chat_id, reply.text)
            return
        self._handle_review(chat_id, prompt, reply.text)

    def _handle_review(self, chat_id: int, prompt: str, introduction: str = "") -> None:
        try:
            plan_name = self._review_mission(prompt)
        except Exception:
            self._send_message(chat_id, "The NIM mission proposal was refused or is unavailable. No PX4 connection was opened.")
            return
        self._pending_plans[chat_id] = plan_name
        self._send_message(chat_id, f"{introduction}\nElkészítettem egy safety-approved tervet. Indítsam a szimulációban?")

    def _start_pending_plan(self, chat_id: int) -> None:
        plan_name = self._pending_plans.pop(chat_id)
        try:
            result = self._execute_plan(plan_name)
        except Exception:
            self._send_message(chat_id, "Nem tudtam elindítani a szimulációt. A drón nem kapott parancsot.")
            return
        self._send_message(chat_id, f"Rendben, a küldetést {result}. Figyelem az állapotát.")

    def _handle_execute(self, chat_id: int, command: str) -> None:
        parts = command.split()
        if len(parts) != 2 or not _PLAN_NAME.fullmatch(parts[1]):
            self._send_message(chat_id, "Use /execute <reviewed plan filename> from the previous approval message.")
            return
        plan_name = parts[1]
        try:
            result = self._execute_plan(plan_name)
        except Exception:
            self._send_message(chat_id, "SITL execution was not started. The reviewed plan and safety checks were left unchanged.")
            return
        self._send_message(chat_id, f"SITL execution {result}: {plan_name}")


def run_bot(configuration: TelegramBotConfiguration) -> None:
    """Long-poll Telegram. The server is intentionally not exposed as a webhook."""
    client = TelegramClient(configuration.token)
    gateway = TelegramMissionGateway(
        allowed_chat_ids=configuration.allowed_chat_ids,
        send_message=client.send_message,
        review_mission=_review_with_cli,
        execute_plan=_execute_with_cli,
        converse=_converse_with_nim,
    )
    offset: int | None = None
    while True:
        for update in client.get_updates(offset):
            update_id = update.get("update_id")
            if isinstance(update_id, int):
                offset = update_id + 1
            gateway.handle_update(update)


class TelegramClient:
    """Small HTTPS-only Bot API client; the token never reaches a browser."""

    def __init__(self, token: str) -> None:
        self._base_url = f"{_TELEGRAM_API}/bot{token}"

    def get_updates(self, offset: int | None) -> tuple[dict[str, object], ...]:
        query: dict[str, str | int] = {"timeout": 25, "allowed_updates": json.dumps(["message"])}
        if offset is not None:
            query["offset"] = offset
        response = self._request("getUpdates", query=query)
        result = response.get("result")
        if not isinstance(result, list):
            raise TelegramGatewayError("Telegram returned an invalid updates response.")
        return tuple(item for item in result if isinstance(item, dict))

    def send_message(self, chat_id: int, text: str) -> None:
        self._request("sendMessage", body={"chat_id": chat_id, "text": text})

    def _request(
        self, method: str, *, query: Mapping[str, str | int] | None = None, body: Mapping[str, object] | None = None
    ) -> dict[str, Any]:
        url = f"{self._base_url}/{method}"
        if query:
            url = f"{url}?{urlencode(query)}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST" if data else "GET")
        try:
            with urlopen(request, timeout=35) as response:
                payload = json.loads(response.read(128 * 1024))
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as error:
            raise TelegramGatewayError("Telegram API is unavailable or returned invalid JSON.") from error
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise TelegramGatewayError("Telegram API rejected the request.")
        return payload


def _review_with_cli(prompt: str) -> str:
    completed = subprocess.run(
        [sys.executable, "-m", "brain.cli.fly_nim_mission", "--command", prompt],
        check=True,
        capture_output=True,
        text=True,
        timeout=45,
    )
    match = re.search(r"^Reviewed plan: (.+)$", completed.stdout, flags=re.MULTILINE)
    if match is None:
        raise TelegramGatewayError("Mission review did not produce a plan.")
    plan_path = Path(match.group(1)).resolve()
    expected_directory = _DEFAULT_PLAN_DIRECTORY.resolve()
    if plan_path.parent != expected_directory or not _PLAN_NAME.fullmatch(plan_path.name):
        raise TelegramGatewayError("Mission review returned an unexpected plan path.")
    return plan_path.name


def _converse_with_nim(text: str) -> ConversationReply:
    """Let NIM decide whether speech is conversation or a high-level mission request.

    This route can only request planning; it cannot execute, select a plan, or
    access PX4. The Telegram state machine still requires the user's later
    natural-language confirmation.
    """
    agent = NIMMissionAgent.from_environment()
    payload = {
        "model": agent._model,
        "temperature": 0.2,
        "max_tokens": 300,
        # Nemotron reasoning tokens can otherwise consume the small response
        # before it reaches the forced routing tool call.
        "reasoning_budget": 0,
        "messages": [
            {"role": "system", "content": "Te ByteWolf vagy, egy barátságos magyar drón-asszisztens a szimulációban. Természetesen, első személyben beszélj magyarul: a felhasználó jogosan gondolhat rád úgy, mint aki a drón testén keresztül mozog. Ha repülést kér, ismerd el a szándékot és jelezd, hogy biztonságos tervet készítesz, majd csak külön jóváhagyás után indítható. Soha ne állítsd, hogy már repülsz, hozzáfértél személyes adatokhoz vagy motorokat vezéreltél; ezeket a rendszer külön engedélyezi. A flight task explicit drónmozgásra, felszállásra, odarepülésre, követésre, leszállásra vagy megfigyelésre irányuló kérés. A felszállás–lebegés–leszállás kérés helyszín nélkül is egyértelmű SITL feladat. Csak ismeretlen célhely, például „a boltba” esetén kérj pontos helyi célt és maradjon requests_drone_action=false. A reply kizárólag embernek szóló természetes magyar mondat legyen: soha ne említs toolt, JSON-t, mezőnevet vagy requests_drone_action értéket. Call route_conversation exactly once."},
            {"role": "user", "content": text},
        ],
        "tools": [{"type": "function", "function": {"name": "route_conversation", "description": "Reply naturally and state whether the user is asking for a drone flight action.", "parameters": {"type": "object", "additionalProperties": False, "required": ["reply", "requests_drone_action"], "properties": {"reply": {"type": "string"}, "requests_drone_action": {"type": "boolean"}}}}}],
        "tool_choice": {"type": "function", "function": {"name": "route_conversation"}},
    }
    response = agent._post_json(f"{agent._base_url}/chat/completions", {"Authorization": f"Bearer {agent._api_key}", "Content-Type": "application/json"}, payload, 30.0)
    try:
        call = response["choices"][0]["message"]["tool_calls"][0]["function"]
        routed = json.loads(call["arguments"])
        reply = routed["reply"]
        requested = routed["requests_drone_action"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as error:
        raise TelegramGatewayError("NIM conversation response was invalid.") from error
    if not isinstance(reply, str) or not isinstance(requested, bool):
        raise TelegramGatewayError("NIM conversation response had invalid fields.")
    return ConversationReply(reply[:_MAX_MESSAGE_CHARS], requested)


def _execute_with_cli(plan_name: str) -> str:
    plan_path = _DEFAULT_PLAN_DIRECTORY / plan_name
    if not _PLAN_NAME.fullmatch(plan_name) or not plan_path.is_file():
        raise TelegramGatewayError("Reviewed plan does not exist.")
    # Verify the same proof carried by the execution CLI before reporting a
    # submitted run to the operator. This opens neither MAVSDK nor PX4.
    from brain.cli.fly_nim_mission import _load_approved_plan
    from brain.mission_spec.validation import load_mission_safety_profile
    from brain.safety.profile import DEFAULT_SAFETY_PROFILE_PATH

    _load_approved_plan(plan_path, load_mission_safety_profile(DEFAULT_SAFETY_PROFILE_PATH))
    global _active_execution
    with _execution_lock:
        if _active_execution is not None and _active_execution.poll() is None:
            raise TelegramGatewayError("A SITL mission is already running.")
        _active_execution = subprocess.Popen(
            [sys.executable, "-m", "brain.cli.fly_nim_mission", "--mission-spec-file", str(plan_path), "--execute"],
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return "submitted after local safety validation"


def _parse_allowed_chat_ids(raw: str) -> frozenset[int]:
    values: set[int] = set()
    for item in raw.split(","):
        candidate = item.strip()
        if not candidate:
            continue
        try:
            values.add(int(candidate))
        except ValueError as error:
            raise TelegramGatewayError("TELEGRAM_ALLOWED_CHAT_IDS must contain comma-separated integers.") from error
    return frozenset(values)


def _help_text() -> str:
    return (
        "Szia, a ByteWolf drón-asszisztense vagyok (SITL). Beszélj hozzám természetesen.\n"
        "Ha repülést kérsz, először tervet készítek, majd megkérdezem, indítsam-e.\n"
        "Semmilyen üzenet nem vezérli közvetlenül a PX4/MAVLink rendszert."
    )


def main() -> None:
    configuration = TelegramBotConfiguration.from_environment()
    print(
        "Telegram Mission Gateway active; long polling enabled for "
        f"{len(configuration.allowed_chat_ids)} allowlisted private chat(s)."
    )
    run_bot(configuration)


if __name__ == "__main__":
    main()
