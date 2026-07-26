# Workstream E — Speech és kommunikáció (kutatás + adapter-prototípus)

- **Ág:** `feature/speech-adapter`
- **Base:** `codex/v1-stabilization-plan`
- **Függőség:** `feature/plugin-sdk` (a beszéd- és kommunikációs adapterek plugin/
  body-adapter szerződés mögé kerülnek), `feature/cognitive-runtime` (a transcript
  a runtime bemenete).

## Cél

STT/TTS plugin, push-to-talk, interruption handling, valamint Telegram/web/mobile
kommunikációs adapterek. **A beszédfelismerés kizárólag parancsjavaslatot készít,
soha nem hajt végre repülést.** Ez a sprintben elsősorban kutatás + prototípus.

## Kimenetek (sprint)

- STT/TTS opciók összehasonlítása (helyi vs felhő, latency, magyar nyelvi minőség,
  költség) → döntési tábla.
- `Transcript` contract v0.1 vázlat: a kimenet `command_suggestion`, nem parancs.
- Push-to-talk és interruption-handling prototípus-terv.
- Body/kommunikációs adapter interfész vázlat a Plugin SDK szerződéséhez igazítva.

## Verziózott contract terve

- `shared/schemas/speech/transcript_v0_1.schema.json` (prototípus)
- `brain/speech/adapters/` — STT/TTS és kommunikációs adapter stubok

## Safety-határ (nem tárgyalható)

- A speech adapter a Cognitive Runtime **bemenete**, nem kap közvetlen
  flight/actuator utat.
- Egy felismert parancs is a `draft_flight_request` → reviewed MissionSpec →
  SafetyGate → külön jóváhagyás úton megy; a beszéd nem kerülheti meg a gate-et.

## Definition of Done (közös, Notion)

- Verziózott szerződés és kompatibilitási teszt.
- Unit és cross-runtime integrációs tesztek.
- Strukturált audit: input refs, modell, promptverzió, latency, tokenhasználat és
  admission eredmény (ahol értelmezhető).
- Dokumentált timeout, retry, cancellation és fallback.
- Safety boundary regresszió zöld.
- Notion státusz és GitHub Wiki technikai dokumentáció frissítve.

## Elfogadási kritériumok (kutatási sprint)

1. STT/TTS döntési tábla legalább 3 opcióval, magyar nyelvi és latency-oszloppal,
   indokolt ajánlással.
2. `Transcript v0.1` vázlat: a séma csak `command_suggestion` kimenetet enged,
   végrehajtott flightot nem modellez.
3. Push-to-talk + interruption-handling prototípus-terv (állapotgép + megszakítási
   szabályok).
4. Egyértelmű, tesztelt safety-állítás: a beszédből származó parancsjavaslat a
   SafetyGate + jóváhagyás úton megy, közvetlen actuator-út nélkül.
