# Natural-language command gateway

`brain/mission_spec/command_gateway.py` is the front door for a spoken or typed
mission. It turns a bounded natural-language request into a MissionSpec v0.1
document and hands that document to the existing validator, compiler, and
SafetyGate. It opens no PX4 connection and emits no MAVLink, actuator, or motor
command — the words never reach the vehicle unchecked, and the deterministic
safety layer stays the authority that approves or refuses a mission.

## Deterministic fallback, not a language model

The parse is a bounded grammar, so the same request always yields the same
MissionSpec — the `mission_id` is derived from the request text, not a clock or
a random source. Supported V1 intents, in English and the canonical Hungarian
demo phrasing:

| Intent | Example (EN / HU) | Step |
| --- | --- | --- |
| Take off | "take off to 2 m" / "szállj fel 2 méterre" | `TAKEOFF` |
| Go to a local point | "fly 5 m north" / "repülj 5 métert északra" | `GOTO_LOCAL` |
| Go to the designated point | "fly to the designated point" / "repülj a kijelölt ponthoz" | `GOTO_LOCAL` (needs a coordinate) |
| Hold | "hover for 3 seconds" / "lebegj 3 másodpercig" | `HOLD` |
| Return | "come back" / "gyere vissza" | `RTL` |
| Land | "land" / "szállj le" | `LAND` |

"Come back and land" is one action: RTL already lands, so a `LAND` immediately
after an `RTL` is folded into it rather than becoming a forbidden second
terminal step.

The NIM-backed Mission Agent is a separate application boundary documented in
[`nim-mission-agent-v0_1.md`](nim-mission-agent-v0_1.md). It proposes a
MissionSpec through a hosted LLM, then traverses the same deterministic schema,
SafetyGate, and executable-shape gates. This grammar remains useful as a
deterministic fallback and a test oracle; it is not the intended final natural-
language interface.

## Parsing proposes; the safety layer decides

The grammar can only ever propose. Whatever it accepts is still just a document
until `validate_and_compile_mission_spec` approves it, so an over-altitude "take
off to 500 m" parses cleanly and is then refused by the platform ceiling. Every
rejection is structured and names what caused it:

- an unsupported clause names the exact clause text;
- a request that mentions "the designated point" with no coordinate is refused
  as ambiguous;
- a schema or safety breach names the source text and the failed constraint.

A rejected request yields no mission, so nothing downstream can execute it.

## Known limitation

The canonical full demo — take off, go to a point, come back, land — compiles
and passes the safety layer, but its exact shape (takeoff → goto → RTL with no
hold) has no path through the current bounded execution adapters, which support
takeoff-hover-land, waypoint-land, and takeoff-hover-return. The orchestrator
refuses that shape rather than dropping a step; the gap is surfaced by a test,
not hidden. Widening the executable shapes is separate adapter work.

## Boundary

The gateway imports neither MAVSDK nor any flight adapter, enforced by a test.
Its output is a `CompiledMission` — the same object the orchestrator already
routes — or a structured refusal. Execution stays where it was: the orchestrator
and the CLIs, behind the SafetyGate.
