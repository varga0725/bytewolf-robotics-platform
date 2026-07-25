# Pi retirement v0.1

The dashboard conversational turn runs on the Python Cognitive Runtime instead of
the Node Pi runner. See [`docs/cognitive-runtime-v0_1.md`](cognitive-runtime-v0_1.md)
for the runtime itself.

## Shape

```
dashboard chat
  -> PiConversation (apps/agent/pi_conversation.py)
       briefings (capability, world, recalled facts) resolved in Python
       system prompt (apps/agent/pi_prompt.py, a port of prompt.mjs)
  -> CognitiveRuntime.run_turn
       tools = read-only plugins: telemetry.read, vision.summary, world_memory.query
       reserved tool: draft_flight_request (signals intent only)
  -> reply (plain text) + requests_drone_action (= flight intent)
  -> post-turn: Python memory extractor -> cognitive-hooks admission -> canonical store
```

A flight is never compiled or executed in the turn. `draft_flight_request` only
signals intent, which sets `requests_drone_action`; the gateway's existing review
step compiles the natural-language request into a MissionSpec, and it still needs
explicit approval before anything runs.

## Live NIM evidence (2026-07-24)

A manual smoke against real NIM (`nvidia/nemotron-3-nano-30b-a3b`), the credential
path the dashboard uses. Proof level: app + live NIM (not SITL).

- **Read turn** — "Mennyi az akkumulátor töltöttsége? A nevem Ferenc." The model
  called `telemetry.read` and replied with the artifact's real value
  ("87,5 %"); `requests_drone_action` false. The read-tool path works end to end.
- **Flight-intent turn** — "Repülj fel 2 méterre és gyere vissza." The model
  called `draft_flight_request`; the reply acknowledged a plan drafted for review,
  and `requests_drone_action` was true. No plan was compiled or executed in the
  turn; nothing reached actuation.
- **Memory** — the extractor ran and admission returned `skipped`: the nano model
  proposed no durable fact through the forced tool. This is model quality, not a
  code fault (no dedicated `NIM_MEMORY_MODEL` was set); the pipeline validated,
  admitted and stored nothing, as designed.

The smoke also caught a real bug before it shipped: the draft handler had
expected a full MissionSpec the chat model cannot author, which exhausted the
tool loop. Fixed to signal intent only.

## Deliberately not done

- `mission.history` read-only plugin (the current turn has no such tool).
- Node removal (`runner.mjs` and the Node modules/tests, the CI node job, the Pi
  SDK dependency) — done after this smoke, once the Python path is proven live.
