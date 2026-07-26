# Cognitive Runtime

ByteWolf Cognitive Runtime v0.3 — session manager, tool loop, deterministic
response envelope, observability. Charter, contract-terv, Definition of Done és
elfogadási kritériumok: [`docs/workstreams/cognitive-runtime.md`](../../docs/workstreams/cognitive-runtime.md).

Safety-határ: egyetlen runtime tool sem érheti el a MAVSDK/PX4 API-t; a repülés
csak reviewed MissionSpec → SafetyGate → jóváhagyás úton kérhető.
