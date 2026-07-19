# Pi Agent v0.1

The dashboard's conversational layer uses the [Pi SDK](https://pi.dev/docs/latest/sdk)
with NVIDIA NIM. Pi is an agent harness, not the flight controller.

```text
Dashboard/mobile chat
  → FastAPI Command Gateway
  → Pi SDK session + durable local memory
  → reviewed MissionSpec request
  → deterministic validation + SafetyGate
  → explicit dashboard approval
  → MAVSDK / PX4 SITL
```

## Safety boundary

`apps/pi_agent/runner.mjs` starts each turn with only four typed tools:

- `get_drone_state` — reads the dashboard telemetry artifact.
- `get_vision_summary` — reads current detection artifacts.
- `remember_user_fact` — saves one non-sensitive fact only when the user
  explicitly asks the agent to remember it in that same turn. The runner also
  rejects token/password/card-like values, email addresses and street-address
  labels before persistence.
- `draft_flight_request` — requests a reviewed mission plan. It has no PX4,
  MAVSDK, MAVLink, shell, or actuator access.

The last tool is the only way a Pi turn can request a flight plan. The Python
gateway receives that typed outcome, then invokes the pre-existing reviewed
MissionSpec workflow. A browser session must approve the exact pending plan
before the executor can connect to PX4.

The Pi process is one local subprocess per dashboard message. Persistent Pi
sessions and explicitly admitted user facts live under `var/pi-agent/`, which
is intentionally Git-ignored. The browser UUID is the opaque session key; it
is not an authentication system. This is still a local-only development setup.

## Install and run

The Pi SDK is a Node dependency beside the Python application:

```zsh
cd apps/pi_agent
npm ci --ignore-scripts
cd ../..
set -a; source .env; set +a
.venv/bin/python -m apps.api.server
```

`NVIDIA_API_KEY` and `NIM_MISSION_MODEL` must remain in the ignored `.env`
file. `apps/pi_agent/models.json` contains no secret; it declares the NVIDIA
NIM OpenAI-compatible provider and resolves the key from the environment.

## Current memory model

Pi persists the conversation for the browser's local UUID, so follow-up
phrases such as “akkor nézd meg inkább az udvart” retain their conversational
context. The structured memory store deliberately accepts only explicitly
requested, non-sensitive facts such as a preferred name or a place label.

It does not yet contain face identity, autonomous world mapping, external
accounts, address-book access, or a production identity/authentication model.
Sensor observations remain evidence files rather than long-term model memory.
