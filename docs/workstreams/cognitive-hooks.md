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
