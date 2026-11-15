# ByteWolf Command Gateway API

The local FastAPI service is the shared boundary for the session-scoped Control
Room and the later mobile client. It does not yet provide network
Tailscale Serve identity authentication and an operator allowlist. It also serves the separate public marketing
site at `/`. Its conversational layer is a local Pi SDK runner backed
by NVIDIA NIM; it is the only user-interface route that may ask the Mission
Agent to create a plan. It never exposes PX4, MAVSDK, motors, or raw actuator
commands to a browser.

## Run the public site and Control Room

```zsh
cd apps/marketing/frontend && npm ci && npm run build && cd ../../..
cd apps/dashboard/frontend && npm ci && npm run build && cd ../../..
.venv/bin/python -m apps.api.server
```

Open `http://127.0.0.1:8080/` for the public marketing site and
`http://127.0.0.1:8080/control-room/` for the React Control Room. The Control
Room provides read-only state, camera, world evidence, memory, knowledge-graph
and audit-replay views, plus reviewed mission planning. The legacy static
dashboard has been removed.

Only the Control Room uses the mission API boundary: a flight request creates
a session-bound pending plan, and the browser must explicitly approve that
exact plan before the existing safety-gated executor can connect to SITL. The
public site has no telemetry, control, approval, or authentication surface.

## API boundary

- `GET /api/v1/telemetry` — read-only flight state.
- `GET /api/v1/camera`, `GET /api/v1/detections` — read-only vision evidence.
- `GET /api/v1/world-map`, `/world-memory`, `/knowledge` — read-only evidence
  and separate personal/world knowledge context. `/knowledge` is session-bound.
- `GET /api/v1/missions/replays`, `/missions/replays/{run_id}` — validated,
  read-only mission audit replay; these endpoints never contact a vehicle.
- `GET /api/v1/capabilities` — read-only Ground vs SITL Console mode (`commands_enabled`).
- `POST /api/v1/chat` — conversational request; may create a pending plan only when `BYTEWOLF_COMMANDS_ENABLED` is exactly `true` (flight intent only).
- `POST /api/v1/plans/approval-token`, `/approve`, `/cancel` — registered only when `BYTEWOLF_COMMANDS_ENABLED` is exactly `true`. Approve requires a short-lived server token bound to operator and plan hash; operate only on that browser session's pending plan.
  session's pending plan.

Validate the Control Room with `npm run test`, `npm run test:e2e`, and
`npm run build` from `apps/dashboard/frontend`; validate the public site with
its `npm run test` and `npm run build` commands. The Control Room browser E2E
suite mocks only read APIs and does not approve or execute a mission.

Pi persists the conversation and explicitly admitted, non-sensitive user facts
under the Git-ignored `var/pi-agent/` directory, keyed by the browser-generated
local session UUID. That UUID is correlation state, not authentication.

Loopback requests with an allowed `Host` header are attributed to operator
`local`, which means any process on the same machine may call the API — not only
a human at the browser. The HTTP approval-token path is the supported operator
boundary; the CLI demo (`brain/cli/chat_mission_demo.py`) and Telegram gateway
still approve through in-process gateway calls and are documented follow-ups for
the same token discipline.

The server is local-only on `127.0.0.1`; the installer exposes it to the tailnet
only through authenticated Tailscale HTTPS. The gateway pins executable
missions to `--deployment-mode simulation`; physical actuation remains disabled.
See [`docs/pi-agent-v0_1.md`](../../docs/pi-agent-v0_1.md) for the tool and
safety boundary.
