# Pi Agent Memory Hooks v0.2

## Decision

Durable memory is **not** a tool the conversational model chooses to call.
After every completed dashboard or mobile chat turn, a post-turn memory hook
analyses the exact user message and the safe final assistant reply through one
separate NVIDIA NIM LLM call. It returns a constrained memory delta; local,
deterministic code then validates and merges that delta.

This preserves natural conversation without granting the chat model an
unreviewed write capability. It also keeps flight control isolated: neither
the extractor nor the memory store can send MAVLink, call MAVSDK, access a
shell, or approve a mission.

## Turn lifecycle

```text
user message
  -> Pi conversational turn (read-only state + draft-flight tools)
  -> safe final assistant reply
  -> post_turn_memory_hook
  -> NIM memory-delta extraction
  -> deterministic admission and merge
  -> durable session memory for the next turn
```

The hook sees no internal reasoning, tool arguments, raw model transcript,
API keys, or actuator interfaces. It receives only:

```json
{
  "turn_id": "opaque-id",
  "session_id": "opaque-browser-session-id",
  "user_message": "…",
  "assistant_reply": "…"
}
```

The assistant reply is the canonical response emitted through
`respond_to_user`, never an intermediate or hidden model message.

## Extraction contract

The dedicated NIM call is deterministic (`temperature: 0`, no reasoning
budget) and may emit only this typed payload:

```json
{
  "kind": "memory_delta",
  "operations": [
    {
      "op": "upsert",
      "category": "preference",
      "value": "A felhasználó a Baylands világot szeretné alapértelmezettnek."
    }
  ]
}
```

Allowed categories are deliberately narrow in v0.2:

- `name`
- `preference`
- `place_label`
- `relationship`

No operation means no durable update. A later explicit correction supersedes
an earlier fact of the same category. A user request to forget something is
represented by a `forget` operation and is handled before any new upserts.

## Admission and privacy boundary

The model suggests; code decides. The admission gate applies all of these
rules before writing `var/pi-agent/memory/<session>.json`:

- rejects unknown operations and categories;
- caps operation count and field length;
- rejects credentials, tokens, passwords, payment data, e-mail addresses,
  telephone numbers and precise street addresses;
- deduplicates normalized facts and records their source turn;
- accepts only stable user facts, preferences, labels and relationships—not
  mission commands, transient status, instructions or model speculation;
- fails closed: invalid output, timeout, or NIM failure writes nothing.

The hook is non-blocking for the user-facing chat result. If extraction is
unavailable, the safe conversation response still returns normally and the
event is logged without message content or secrets. Session IDs are a local
development identity boundary, not production authentication.

## Sensor and world memory

Vision, LiDAR and future world-map data remain separate, evidence-backed
stores. They require source timestamp, sensor origin, confidence and expiry.
The v0.2 personal-memory hook must not turn a detection into identity,
location, or biometric memory. Face identification is explicitly out of scope.

## Implementation steps

1. Remove the model-callable `remember_user_fact` tool.
2. Add `post_turn_memory_hook` with strict NIM structured output and timeout.
3. Implement deterministic schema validation, sensitive-data rejection,
   merge, deduplication and `forget` semantics.
4. Add Node unit tests for admission and hook failure isolation; keep the
   Python gateway integration tests green.
5. Surface a privacy-safe memory status in diagnostics only, not in chat.
6. Add explicit user controls to inspect, correct and erase their memory. **Done
   locally:** `GET`, `PUT` and `DELETE /api/v1/memory` are session-scoped and
   the dashboard presents the admitted facts with edit/delete controls.
7. Add a separate evidence graph for spatial map, mission history and future
   vision/LiDAR observations; never mix it with personal memory by default.

## Acceptance criteria

- Every completed conversational turn attempts exactly one isolated memory
  extraction call.
- The primary Pi agent has no memory-write tool.
- A failed or malicious extraction cannot alter memory or change a flight
  decision.
- The next turn can use admitted facts, while a user can correct or delete
  them.
- Dashboard/mobile remains the primary interface; Telegram is only another
  adapter to the same future turn lifecycle.
