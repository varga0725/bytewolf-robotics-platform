# Workstream — Cognitive Hooks (háttér-LLM job runtime)

- **Ág:** `feature/cognitive-hooks`
- **Base:** `codex/v1-stabilization-plan`
- **Függőség:** shared contractok. A `feature/world-model-v0.2` erre épül (downstream).

## Cél

Az agent ne csak chatfordulóra reagáljon: biztonságos háttér-LLM munka egységes,
determinisztikus admission-pipeline mögött. A meglévő
`post_turn_memory_hook` (`docs/pi-memory-hooks-v0_2.md`) ennek első esete és
referencia-implementációja — általánosítva proposal-alapú runtime-má.

## Kimenetek

- Háttér-LLM job runtime.
- Proposal store.
- Admission pipeline: `proposal → schema validation → policy/admission → canonical store`.

## Verziózott contract terve

- `shared/schemas/cognitive_hooks/proposal_v0_1.schema.json`
- `shared/interfaces/cognitive_hooks/examples/{valid,invalid}/…`
- Runtime: `brain/cognitive_hooks/{runtime,proposal_store,admission}.py`

## Safety-határ (nem tárgyalható)

- A hook, a proposal és az admission nem érhet el MAVSDK/PX4/actuator API-t.
- Háttérmunkából közvetlen flight action nem keletkezhet.
- Fail-closed: érvénytelen, timeoutolt vagy malformed proposal semmit nem ír.
- A személyes memória és az evidence-backed world store külön marad (nincs közös
  fájl, kategória vagy kódút); face-identity továbbra is out of scope.

## Definition of Done (közös, Notion)

- Verziózott szerződés és kompatibilitási teszt.
- Unit és cross-runtime integrációs tesztek.
- Strukturált audit: input refs, modell, promptverzió, latency, tokenhasználat és
  admission eredmény.
- Dokumentált timeout, retry, cancellation és fallback.
- Safety boundary regresszió zöld.
- Notion státusz és GitHub Wiki technikai dokumentáció frissítve.

## Elfogadási kritériumok (workstream-specifikus)

1. Minden LLM háttérmunka a `proposal → schema validation → policy/admission →
   canonical store` útvonalon fut (Notion integrációs mérföldkő 4).
2. `proposal v0.1` verziózott JSON Schema valid/invalid fixture-párokkal.
3. A meglévő `post_turn_memory_hook` az új runtime-on fut, funkcionális regresszió
   nélkül: `apps/pi_agent/post_turn.test.mjs` és `tests/test_pi_memory_hook.py`
   zöldek maradnak (vagy átvezetve az új runtime-ra).
4. Fail-closed bizonyítva: hibás extractor / hibás írás / timeout semmit nem ír és
   `unavailable`-t jelent.
5. Admission audit: minden proposal döntése naplózott (`accepted`/`rejected` + ok),
   input refs + modell + promptverzió mellett.
6. Statikus teszt: `brain/cognitive_hooks/**` nem importál MAVSDK/PX4 modult.

## Állapot (v0.1 mag kész, Pi-hook átvezetés hátravan)

| Modul | Tartalom | Commit |
| --- | --- | --- |
| `contracts.py` + `proposal_v0_1` séma + fixture-ök | Proposal contract, fail-closed loader | `6c7bc7c` |
| `admission.py` | Determinisztikus admission (caps, érzékeny-adat, dedup, forget-előbb) | `6c7bc7c` |
| `runtime.py` | `HookRuntime.submit` + `ProposalStore` (kanonikus store + audit) | `6c7bc7c` |

- [x] 1. `proposal → schema validation → policy/admission → canonical store` út.
- [x] 2. `proposal v0.1` verziózott séma valid/invalid fixture-párokkal.
- [x] 4. Fail-closed: malformed/hibás dokumentum semmit nem ír, `unavailable`.
- [x] 5. Admission audit: minden döntés naplózott (`accepted`/`rejected` + ok); a proposal
  hordozza az `input_refs`/modell/promptverzió provenance-t.
- [x] 6. Statikus no-MAVSDK/PX4 teszt.

**Pi-hook átvezetés (kész):**

- [x] 3. A meglévő `post_turn_memory_hook` (Node) átvezetve a Python runtime-ra
  regresszió nélkül: `brain/cognitive_hooks/memory_hook.py` (`run_post_turn_memory`)
  tükrözi a Node szerződését. Az admission és a store-merge a `memory.mjs`-hez
  igazítva (6 op / 240 karakter / truncate / name-supersede / store-cap 40 /
  azonos érzékeny-minta). Node-gated **cross-runtime parity teszt** bizonyítja,
  hogy a Node és a Python hook azonos státuszszót ad 7 esetre.
- [x] A Node suite 24/24 zöld, a `tests/test_pi_memory_hook.py` érintetlenül zöld.

**Production cutover (Codex P1 megoldva):** a live dashboard-út is az új runtime-on
fut. A Node `runner.mjs` már csak *extractor* (a nyers deltát adja vissza); a Python
`apps/agent/pi_memory.py::PiMemoryHook` validál (`load_proposal`) + admittál (`admit`)
+ a **megosztott kanonikus store**-ba ír (a dashboard memory-API formátumában, a
`memory_store` helpereit újrahasználva). `PiAgentClient` opcionális `memory_hook`-ot
kap, a `server.py` élesben bekötve. A briefing-olvasás változatlan (közös formátum).

Teljes Python suite 847 zöld, Node suite 24/24. A Codex review 3×P2-je is javítva
(forget-majd-upsert supersede, immutable facts, audit-provenance).
