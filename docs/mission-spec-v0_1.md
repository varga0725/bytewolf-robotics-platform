# MissionSpec v0.1

A MissionSpec a ByteWolf magas szintű, verziózott küldetésszerződése. A
Mission Planner ezt állítja elő, a determinisztikus validátor pedig még a
Mission Orchestrator és a MAVSDK-adapter előtt vizsgálja meg.

## Biztonsági határ

A v0.1 csak a következő lépéseket fogadja el:

- `TAKEOFF`
- `GOTO_LOCAL` (észak/kelet/le, NED formában)
- `HOLD`
- `LAND`
- `RTL`

Nincs benne nyers MAVLink üzenet, aktuátor-, motor- vagy offboard parancs. A
fordító csak immutábilis `TakeoffCommand`, `WaypointCommand`, `LandCommand` és
`ReturnToHomeCommand` objektumokat hoz létre. Minden létrejött parancs a
meglévő `SafetyGate` ellenőrzésén is átmegy.

## Kötelező szabályok

- `schema_version` értéke pontosan `0.1`.
- A küldetés a futó X500 V2 twin azonosítójára készül.
- A mission korlátai csak szigorúbbak lehetnek a platform korlátainál.
- Pontosan egy, első `TAKEOFF` lépés és egy utolsó `LAND` vagy `RTL` lépés kell.
- A helyi waypoint nem hagyhatja el a mission sugarát.
- A felszállási és waypoint-magasság nem lehet a mission maximuma felett.
- A linkvesztési szabály nem gyengítheti a platform előírt RTL fallbackjét.

A séma: `shared/schemas/mission_spec/mission_spec_v0_1.schema.json`. Egy valid
minta: `shared/interfaces/mission_spec/examples/takeoff_waypoint_rtl.v0_1.json`.

## Jelenlegi megvalósítási állapot

A séma, a determinisztikus validátor, a fordító és a korlátos Mission
Orchestrator elkészült. Az orchestrator csak veszteségmentesen ábrázolható
alakokat futtat: takeoff–hold–land, takeoff–waypoint–hold–land és
takeoff–hold–RTL. A NIM Mission Agent CLI ezeket a jóváhagyott MissionSpec
dokumentumokat futtatja; minden más valid, de még nem végrehajtható alakot
PX4-kapcsolat előtt elutasít.

A telemetria-alapú preflight feltételek, az abort policy végrehajtása és a
teljes retry policy a következő szakasz feladatai. A séma ezért nem állítja,
hogy a sebesség- és akkumulátorkorlátot a jelenlegi MAVSDK-adapter futás közben
érvényesíti: ezeket jelenleg a MissionSpec/platform szerződés validálja.


# MissionSpec v0.2 — SURVEY_AREA

v0.1 is frozen. A document says what its own version said it meant, so the
survey step lives in a second schema
(`shared/schemas/mission_spec/mission_spec_v0_2.schema.json`) and the version
in the document selects its validator. An unknown version is refused, never
guessed at.

`SURVEY_AREA` is the first step that states an **area** instead of a place:

```json
{"type": "SURVEY_AREA", "centre_north_m": 0, "centre_east_m": 0,
 "radius_m": 30, "spacing_m": 10, "altitude_m": 6}
```

It stays **one step in the document and many commands in the compiler**. The
human reviewing it should read "sweep 30 m around here"; the SafetyGate must
still see every waypoint individually, because hiding the waypoints from the
gate would be the whole point of the gate, gone. That is also why the spec's
8-step limit is untouched: the expansion happens after review, not in the
document.

`brain/mission_spec/survey.py` generates a boustrophedon sweep clipped to the
circle, and every bound in it is a **refusal, not a clamp**:

- spacing outside 1–15 m is refused — below it the flight is mostly turns,
  above it the sweep stops being a survey;
- more than 60 waypoints is refused rather than silently coarsened, because a
  quietly widened spacing is a survey with holes in it that still reports
  success;
- the radius check is on the **reach** (`hypot(centre) + radius`), not on the
  centre: a 30 m sweep centred 40 m out reaches 70 m, so against a 50 m limit it
  would otherwise pass while flying half again as far as allowed. The numbers
  are an illustration; the limit in force is whatever `twin.yaml` says.

## Mapping while flying

Flying the pattern is not surveying. `simulation/perception/survey_recorder.py`
is a separate read-only observer: it reads the lidar topic and the dashboard
telemetry snapshot, pairs each scan with where the vehicle was and which way it
faced, and records world-memory claims. It is a separate process on purpose —
the mission adapter talks to MAVSDK and nothing else, so mapping can never
slow, block or fail a flight; the worst a broken observer does is remember less.

Pairing is fail-closed. A scan is placed only when a fresh pose exists for it:
a missing heading is not north, a stale position is not the current one, and
either would put a wall somewhere nobody measured. Unpaired scans are counted
and reported, not dropped in silence.

This is why the relay now publishes `heading_deg` when the vehicle has an
attitude fix. It is absent, never zero, when it does not.
