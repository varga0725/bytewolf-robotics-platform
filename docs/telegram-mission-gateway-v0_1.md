# Telegram Mission Gateway v0.1

The Telegram bot is the conversational entry point to the NVIDIA NIM Mission
Agent for **PX4 SITL only**. It is not a flight-control endpoint. The bot does
not import MAVSDK or send MAVLink; it starts the existing, safety-gated CLI.

## Configure

Create a bot with BotFather, then add these local, Git-ignored values to
`.env`. `TELEGRAM_ALLOWED_CHAT_IDS` is mandatory: only these numeric **private
user** chat IDs are allowed to invoke NIM or start a simulator mission.

```sh
TELEGRAM_BOT_TOKEN=replace-with-bot-token
TELEGRAM_ALLOWED_CHAT_IDS=123456789
```

Obtain your private numeric chat ID from a trusted Telegram ID utility. Group,
channel and forwarded messages are rejected even if a matching ID appears in
the allowlist; the sender ID must equal the private chat ID.

Start the bot with the NIM configuration already present in `.env`:

```zsh
set -a; source .env; set +a
.venv/bin/python -m apps.gateway.telegram_mission_gateway
```

The bot uses long polling over HTTPS; it creates no public webhook or inbound
HTTP listener. The terminal prints `Telegram Mission Gateway active...` when
configuration is accepted. Then send `/start` to the bot from the allowlisted
private chat; its help message is the end-to-end health check.

## Conversation and execution boundary

Send a normal Hungarian or English message, for example:

```text
Szállj fel 2 méterre, lebegj 3 másodpercig, majd szállj le.
```

This is identical to `/mission <request>` and produces a reviewed MissionSpec
plus a hash-bound approval record. It opens no PX4 connection. Review the
returned plan filename, start PX4/Gazebo SITL, then explicitly send the exact
second command the bot returns:

```text
/execute <uuid>.mission-spec.json
```

`/execute` accepts only a UUID-named plan in the gateway's generated artifact
directory. Before it starts the execution process, the gateway verifies the
approval sidecar hash, MissionSpec schema, active safety profile and executable
shape; the CLI repeats those checks before it connects to PX4. Any edited,
missing or unapproved plan is refused.

Use `/help` at any time. Unauthorized chats receive no response and trigger no
NIM request or simulator action.

## Current scope

The bot can create and start only the existing bounded SITL mission shapes:

- `TAKEOFF → HOLD → LAND`
- `TAKEOFF → GOTO_LOCAL → HOLD → LAND`
- `TAKEOFF → HOLD → RTL`

It has no real-drone mode. Hardware control requires separate authorization,
authentication, audit retention and physical flight-readiness validation.
