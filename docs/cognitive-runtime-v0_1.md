# Cognitive Runtime v0.1

A supervised, Python-native agent runtime. It replaces the subprocess-per-turn
Pi harness with a controllable turn loop that keeps the existing safety boundary:
it produces one deterministic result per turn, calls tools only through the
Plugin SDK under a ToolPolicy, and can never reach actuation.

## Why Python, not the Pi SDK

Pi (`@earendil-works/pi-coding-agent`) is TypeScript-only; there is no Python SDK
(open request: earendil-works/pi#4174), only Node and an RPC mode. So the runtime
calls NVIDIA NIM directly over its OpenAI-compatible API from Python, and its
tools are Plugin SDK capabilities. The Node Pi Agent is a legacy adapter, not the
core — this is architecture "A" from the parallel plan.

## Turn flow

```
run_turn(session, user_message, tool_policy)
  → provider.complete(history, tool_specs)         # NIM (+ fallback / retry)
  → for each tool call:
       draft_flight_request → reserved handler      # draft only, never actuates
       otherwise            → registry.invoke under ToolPolicy (timeout/rate/concurrency)
     record a tool_trace entry (arg reference, never raw args)
  → repeat until a text reply, or a bounded outcome
  → ResponseEnvelope  (completed | refused | timeout | cancelled | error)
```

Every path returns exactly one schema-valid `ResponseEnvelope`, so a caller never
tells an exception apart from a result.

## Contracts

- `response_envelope_v0_1` — the deterministic turn result: `status`, `model`,
  `provider`, `prompt_version`, `reply`, `latency_ms`, `token_usage`,
  `tool_trace`, `safety_verdict`. `safety_verdict.reached_actuation` is
  `const false`: a runtime that ever claimed otherwise would emit an invalid
  envelope.
- `tool_trace_v0_1` — one call: `capability_id`, `status`
  (`ok`/`denied`/`error`/`timeout`), `latency_ms`, and `args_ref` (a hash),
  never the raw arguments.

## Components

| Module | Role |
| --- | --- |
| `contracts.py` | Envelope + tool-trace loaders (fail-closed, format-checked) |
| `providers.py` | `NIMProvider`, `CircuitBreaker`, `FallbackProvider`, `RetryingProvider` |
| `session.py` | `CognitiveRuntime.run_turn`, session manager, the reserved draft-flight tool |
| `limits.py` | `LimitEnforcer` — ToolPolicy rate + concurrency |
| `artifacts.py` | `persist_envelope` — one versioned JSON audit artifact per turn |
| `apps/agent/cognitive_pi.py` | The Pi Agent adapter |

## The reserved draft-flight tool

`draft_flight_request` is the one tool that is **not** a plugin capability (the
Plugin SDK forbids the `flight` namespace). It is handled specially: the injected
handler drafts a MissionSpec, runs it through `validate_and_compile_mission_spec`
— the same validation the flight CLIs use — and files an accepted mission as a
*pending reviewed plan*. An unsafe mission is refused there. The handler never
calls a flight adapter; execution still requires a separate, explicitly approved
process. `safety_verdict.flight_drafted` records that a draft happened;
`reached_actuation` stays false.

## Safety boundaries (enforced, not conventional)

- No runtime tool reaches MAVSDK/PX4; a static test forbids the import in
  `brain/cognitive_runtime`.
- `reached_actuation` is `const false` in the envelope schema.
- A capability the ToolPolicy did not grant is never called (a `denied` trace
  entry); rate/concurrency/timeout limits are enforced at call time.
- The draft-flight path drafts for review only and cannot execute.

## Enforced limits, retry, fallback

- **Timeout** — per-tool `timeout_ms` (ThreadPoolExecutor) and an overall turn
  deadline; an over-running call becomes a `timeout` trace entry.
- **Rate / concurrency** — `LimitEnforcer` honours `rate_per_min` and
  `max_concurrent` per capability, across turns.
- **Retry** — `RetryingProvider` retries one backend on a transient error.
- **Fallback + circuit breaker** — `FallbackProvider` fails over to the next
  backend and skips an open breaker; all-failed yields a fail-closed `error`
  envelope, never a hang.

## What is deliberately not here (v0.1)

Full conversational parity with the Node Pi harness — durable memory, world
briefing, vision — is delivered by the cognitive-hooks and read-only-plugin
milestones, not by re-implementing the harness in Python. This runtime provides
the turn engine, the safety boundary, and the read + draft-flight surface.
