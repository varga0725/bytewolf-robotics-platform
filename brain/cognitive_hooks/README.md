# Cognitive Hooks

Háttér-LLM job runtime, proposal store és admission pipeline. Charter,
contract-terv, Definition of Done és elfogadási kritériumok:
[`docs/workstreams/cognitive-hooks.md`](../../docs/workstreams/cognitive-hooks.md).

Safety-határ: fail-closed admission; a hook nem érheti el a MAVSDK/PX4 API-t, és
háttérmunkából nem keletkezhet flight action.
