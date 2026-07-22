# Workstream A — ByteWolf Plugin SDK és capability registry

- **Ág:** `feature/plugin-sdk` (átmeneti korábbi név: `feature/pi-plugin-sdk`)
- **Base:** `codex/v1-stabilization-plan` (a tesztelt v1 állapot, nem a `main`)
- **Függőség:** shared, verziózott contractok. Ez a réteg a függőségi gráf gyökere:
  a Cognitive Runtime, az Event Bus és a body/kommunikációs adapterek erre épülnek.

## Cél

Minden integráció egységes, verziózott plugin-szerződés mögé kerüljön. A plugin
soha nem kap közvetlen utat a valós idejű repülési loophoz — csak a manifestjében
deklarált, allowlistelt capabilityket kapja meg.

## Kimenetek

- `PluginManifest`, `Capability`, `ToolPolicy`, `PluginHealth` típusok.
- Lifecycle: `register` / `start` / `stop` / `health`.
- Verziózás, jogosultságok, dependency- és conflict-kezelés.

## Verziózott contract terve

A meglévő minta követendő (`shared/schemas/observation/observation_v0_1.schema.json`
+ valid/invalid fixture-párok + `additionalProperties: false` + `const: "v0.1"`):

- `shared/schemas/plugin_sdk/plugin_manifest_v0_1.schema.json`
- `shared/schemas/plugin_sdk/capability_v0_1.schema.json`
- `shared/schemas/plugin_sdk/tool_policy_v0_1.schema.json`
- `shared/schemas/plugin_sdk/plugin_health_v0_1.schema.json`
- `shared/interfaces/plugin_sdk/examples/{valid,invalid}/…`
- Loader/registry: `brain/plugin_sdk/{manifest,capability,policy,registry,lifecycle}.py`

## Safety-határ (nem tárgyalható)

- A plugin SDK, a registry és semmilyen plugin nem importálhat és nem hívhat
  `brain/adapters/mavsdk_adapter.py`-t, MAVSDK-t, MAVLinket vagy PX4-et.
- A MAVSDK/PX4/actuator capability nem regisztrálható: az allowlist tiltja.
- `brain/safety/gate.py` és `twin.yaml` marad az egyetlen limitforrás; a plugin
  réteg nem duplikál safety-limitet.

## Definition of Done (közös, Notion)

- Verziózott szerződés és kompatibilitási teszt.
- Unit és cross-runtime integrációs tesztek.
- Strukturált audit: input refs, modell, promptverzió, latency, tokenhasználat és
  admission eredmény (ahol értelmezhető).
- Dokumentált timeout, retry, cancellation és fallback.
- Safety boundary regresszió zöld.
- Notion státusz és GitHub Wiki technikai dokumentáció frissítve.

## Elfogadási kritériumok (workstream-specifikus)

1. `PluginManifest v0.1` JSON Schema `const: "v0.1"`, `additionalProperties: false`,
   valid/invalid fixture-párokkal; jövőbeli verziójú manifestet a mai loader elutasít.
2. Capability registry teljes lifecycle-t ad (`register/start/stop/health`);
   duplikált capability és verzió-conflict determinisztikusan elutasításra kerül,
   nem részlegesen indul.
3. `ToolPolicy`: egy plugin kizárólag a manifestjében deklarált capabilityket
   kapja meg; a repülés-vezérlési capability nem regisztrálható (allowlist),
   teszttel bizonyítva.
4. Dependency resolution: hiányzó vagy körkörös függőség a betöltést blokkolja.
5. Statikus teszt bizonyítja, hogy `brain/plugin_sdk/**` nem importál MAVSDK/PX4
   modult.

## Állapot (v0.1 mag kész)

A mag három csomagban elkészült; mind az öt elfogadási kritérium teljesült.

| Csomag | Modul | Commit |
| --- | --- | --- |
| 1 — contractok | `brain/plugin_sdk/contracts.py` + 4 séma + 12 fixture | `a04250d` |
| 2 — registry + lifecycle | `brain/plugin_sdk/registry.py` | `0e88afc` |
| 3 — ToolPolicy-engine | `brain/plugin_sdk/policy.py` | `9e12257` |

- [x] 1. PluginManifest v0.1 séma + valid/invalid fixture; jövőbeli verzió elutasítva.
- [x] 2. Registry teljes lifecycle (`register/start/stop/reload/health`); duplikált
  capability és verzió-conflict determinisztikus elutasítás; nem részlegesen indul.
- [x] 3. ToolPolicy fail-closed (`requests ∩ allowlist → grant/deny`); flight-control
  capability sem nem regisztrálható, sem nem grantelhető.
- [x] 4. Dependency resolution: hiányzó/körkörös függőség blokkol.
- [x] 5. Statikus teszt: `brain/plugin_sdk/**` nem importál MAVSDK/PX4-et.

Safety-réteg háromszorosan drótozva: (1) az `access` enumban nincs aktuációs érték
→ séma szinten kizárt; (2) `FORBIDDEN_CAPABILITY_NAMESPACES` denylist regisztrációnál
és grantnál; (3) statikus no-import teszt. 56 új teszt, teljes suite zöld.

### A teljes DoD-ből a Cognitive Runtime-ra átnyúló rész

Ezek szándékosan a fogyasztó rétegnél záródnak, mert a plugin-sdk csak *deklarál*:

- **Limitek kikényszerítése** (timeout/rate/concurrency) — a ToolPolicy `limits`-ként
  deklarálja; a végrehajtás a Cognitive Runtime (workstream B) feladata.
- **Strukturált audit + cross-runtime integráció** — amikor a Runtime ténylegesen
  fogyasztja a plugin-sdk-t.
