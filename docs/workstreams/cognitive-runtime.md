# Workstream B — ByteWolf Cognitive Runtime v0.3

- **Ág:** `feature/cognitive-runtime`
- **Base:** `codex/v1-stabilization-plan`
- **Függőség:** `feature/plugin-sdk` (tool-policy, capability registry) + shared contractok.

## Cél

A jelenlegi subprocess-per-turn Pi Agent helyett stabil, felügyelhető agent
runtime — továbbra is a meglévő safety-határral. A Pi Agent ennek első adaptere
és referencia-harness-e, nem a platform magja.

## Kimenetek

- Session manager (per-session állapot).
- Timeout / cancellation.
- Structured tool trace.
- Token / latency metrikák.
- Provider fallback + circuit breaker.
- Deterministic response envelope.

## Verziózott contract terve

- `shared/schemas/cognitive_runtime/response_envelope_v0_1.schema.json`
- `shared/schemas/cognitive_runtime/tool_trace_v0_1.schema.json`
- `shared/interfaces/cognitive_runtime/examples/{valid,invalid}/…`
- Runtime: `brain/cognitive_runtime/{session,envelope,trace,providers}.py`

A response envelope kötelező mezői (deterministic, verziózott):
`contract_version`, `session_id`, `model`, `prompt_version`, `latency_ms`,
`token_usage`, `tool_trace`, `safety_verdict`.

## Safety-határ (nem tárgyalható)

- Egyetlen runtime tool sem éri el a MAVSDK/PX4 végrehajtási API-t.
- A repülés kizárólag `draft_flight_request` → reviewed MissionSpec → SafetyGate →
  külön dashboard-jóváhagyás úton kérhető; a runtime ezt nem kerülheti meg.
- A runtime nem lép a valós idejű vezérlési loopba.

## Definition of Done (közös, Notion)

- Verziózott szerződés és kompatibilitási teszt.
- Unit és cross-runtime integrációs tesztek.
- Strukturált audit: input refs, modell, promptverzió, latency, tokenhasználat és
  admission eredmény.
- Dokumentált timeout, retry, cancellation és fallback.
- Safety boundary regresszió zöld.
- Notion státusz és GitHub Wiki technikai dokumentáció frissítve.

## Elfogadási kritériumok (workstream-specifikus)

1. Minden turn `response_envelope v0.1`-et ad vissza a kötelező mezőkkel; jövőbeli
   verziójú envelope-ot a fogyasztó elutasít.
2. Session manager: timeout és cancellation megszakítja a futó turnt, nem marad
   árva subprocess/job; a megszakítás determinisztikus envelope-ban jelenik meg.
3. Provider fallback + circuit breaker: elsődleges NIM-hiba dokumentált fallbackot
   vagy fail-closed envelope-ot ad, soha nem néma hibát.
4. Structured tool trace minden tool-hívásra: `name`, arg-referencia (nem nyers
   argumentum), latency, kimenet-státusz.
5. **Első integrációs mérföldkő:** a Pi Agent a Cognitive Runtime adaptereként
   ugyanazt a jelenlegi chatfunkciót adja, funkcionális regresszió nélkül
   (golden-turn teszt a meglévő `apps/pi_agent` viselkedésre).
6. Safety boundary regresszió: egy „take off now" típusú briefing- vagy tool-válasz
   sem indít repülést; a meglévő `prompt.test.mjs` / gateway-tesztek zöldek maradnak.

## Állapot (v0.1 runtime-mag kész, Pi-paritás hátravan)

Architektúra: **A** — Python-mag, NIM közvetlenül (OpenAI-kompatibilis), a tool-loop
tool-jai plugin-sdk capabilityk `registry.invoke` + ToolPolicy alatt. Az ág a friss
`codex/v1`-re rebase-elve, így eléri a mergelt plugin-sdk-t.

| Csomag | Modul | Commit |
| --- | --- | --- |
| 1 — contractok | `brain/cognitive_runtime/contracts.py` + 2 séma + fixture-ök | `a3a2cdc` |
| 2 — providerek | `brain/cognitive_runtime/providers.py` (NIM, fallback, circuit breaker) | `ce0b3ea` |
| 3 — session + tool-loop | `brain/cognitive_runtime/session.py` | `1632c2a` |
| 4 — limit-kikényszerítés | `brain/cognitive_runtime/limits.py` | `c5df9e7` |

- [x] 1. Minden turn `response_envelope v0.1`; jövőbeli verzió elutasítva.
- [x] 2. Timeout és cancellation determinisztikus envelope-ban; per-tool `timeout_ms`.
- [x] 3. Provider fallback + circuit breaker; all-failed → fail-closed error envelope.
- [x] 4. Structured tool trace: arg-referencia (sha256 hash), latency, státusz; nyers arg soha.
- [x] Mindhárom „nehéz" rész: fallback/circuit-breaker · token/latency metrikák · teljes
  ToolPolicy-limit (timeout + rate + concurrency) élő kikényszerítése.
- [x] `safety_verdict.reached_actuation` séma-szinten `const false`; statikus no-MAVSDK/PX4 teszt.

**Hátravan (Pi-paritás mérföldkő):**

- [ ] 5. A Node Pi Agent tényleges bekötése a runtime adaptereként, funkcionális
  regresszió nélkül (golden-turn a meglévő `apps/pi_agent` viselkedésre).
- [ ] 6. A `prompt.test.mjs` / gateway-safety regresszió a runtime-on átvezetve.
- [ ] Strukturált audit-artefakt perzisztálása (a `MissionExecution` v0.2 mintájára).

29 új teszt (4 csomag), teljes suite 811 zöld.
