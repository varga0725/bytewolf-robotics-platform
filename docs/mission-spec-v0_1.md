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

A séma, a determinisztikus validátor és a fordító elkészült. A fordító
immutábilis magas szintű parancsokat és a HOLD időtartamait állítja elő, de
ezeket még nem hajtja végre generikus Mission Orchestrator. A jelenlegi CLI-k
külön, előre rögzített takeoff/waypoint/RTL küldetéseket futtatnak; nem töltenek
be és nem futtatnak MissionSpec dokumentumot.

A telemetria-alapú preflight feltételek, az abort policy végrehajtása és a
teljes retry policy a következő szakasz feladatai. A séma ezért nem állítja,
hogy a sebesség- és akkumulátorkorlátot a jelenlegi MAVSDK-adapter futás közben
érvényesíti: ezeket jelenleg a MissionSpec/platform szerződés validálja.
