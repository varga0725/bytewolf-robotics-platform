# Observation Contract v0.1

A verziózott szerződés, amely a telemetriát, a state estimationt, a perceptiont
és a későbbi local plannert összeköti. Egy observation azt írja le, **ami van** —
soha nem parancsol.

- Séma: `shared/schemas/observation/observation_v0_1.schema.json`
- Betöltő és érvényesség-feloldó: `brain/telemetry/observation.py`
- Fixture-ök: `shared/interfaces/observation/examples/{valid,invalid}/`

## Ez a szerződés nem vezérlési út

Az observation contract **kizárólag megfigyelés**. Nincs benne — és v0.2-ben sem
lesz — aktuátor-, motor-, arm-, mód- vagy setpoint-parancs. A séma nem ismer
ilyen mezőt, és egy ilyen mezőt hordozó dokumentumot `additionalProperties: false`
alapon elutasít.

A folyamatos lokális vezérlés **külön, saját safety-review-t kapó control
contractot** igényel (frame, rate, max speed, max acceleration, command TTL,
watchdog, fallback action) — az autonómia-roadmap Fázis C-je szerint. A meglévő
telemetry-only ROS 2 bridge sem válik vezérlési úttá attól, hogy ezt a
szerződést beszéli.

## Négy állapot, amit a fogyasztónak meg kell különböztetnie

| Állapot | Honnan jön | Jelentése |
| --- | --- | --- |
| `valid` | producer + kor | A producer megbízik benne, és még friss. **Csak ez használható.** |
| `invalid` | producer | Mért, de nem bízik az eredményben. |
| `missing` | producer | Nincs mérése — ezért **payload sincs**. |
| `stale` | **származtatott** | Méréskor megbízható volt, de túllépte a saját `max_age_s`-ét. |

A `stale` szándékosan nem a producer állítása: **csak a fogyasztó ismeri az
aktuális időt**. A kort az `observed_at` — a mérés pillanata — óta mérjük, nem a
publikálás óta, így egy lassú pipeline nem bújhat el friss publish-idő mögé.

A `usable_payload()` fail-closed: bármi, ami nem `valid`, kivételt dob, nem
pedig „legjobb tudás szerinti" értéket ad vissza.

## Koordinátakeretek: explicit tengelynevek

A frame minden térbeli observationön **kötelező**, és a tengelyek **frame-enként
külön néven** szerepelnek — nem generikus `x/y/z`-ként:

| Frame | Tengelyek | Hol használjuk |
| --- | --- | --- |
| `ned` | `north_m`, `east_m`, `down_m` | PX4/MAVSDK natív; a safety core is north/east-ben gondolkodik |
| `enu` | `east_m`, `north_m`, `up_m` | Gazebo és ROS (REP-103) natív |
| `wgs84` | `latitude_deg`, `longitude_deg`, `absolute_altitude_m` | globális pozíció |
| `frd_to_ned` | `roll_deg`, `pitch_deg`, `yaw_deg` | attitude: test forward-right-down a lokális NED-hez |
| `body_frd` | szektor `yaw_deg` | obstacle: a vázhoz képest, nem északhoz |

**Miért nem `x/y/z`:** így egy frame-tévesztés **sémahiba**, nem csendes
előjelhiba. A projekt épp ezen égett meg: a szél-fixture `north`-nak volt
címkézve, miközben a Gazebo ENU keretében keletre fújt — 58 méteres elsodródás
bizonyította. Egy `north_m` kulcs `enu` frame-ben most elutasításra kerül.

A lokális frame-ek **kötelezően hordozzák az originjukat** (WGS84 pont). Origin
nélkül egy lokális pozíció értelmezhetetlen, ezért nem a launch pontból
következtetjük ki hallgatólagosan.

A sebesség soha nem `wgs84`: fokokban kifejezett keretben egy rátának nincs
értelme.

## Bizonytalanság

Ahol értelmezhető, minden érték mellé opcionális `stddev_*` (egy szórás) tehető.
A `0` érték **állítás**, nem hiány: ha a bizonytalanság ismeretlen, a mezőt ki
kell hagyni, nem nullázni.

## Obstacle: a lefedettség hiánya nem szabad tér

Az obstacle payload szektorokra bomlik, és minden szektor `coverage`-t deklarál:

| `coverage` | Jelentése |
| --- | --- |
| `measured` | Akadály `distance_m` távolságban. |
| `clear` | A szenzor **ellátott** `max_range_m`-ig, és nem talált semmit. |
| `unobserved` | A szenzor **nem tud nyilatkozni** — takarásban, hatótávon kívül, vagy hibás. |

A roadmap szigorú alapértelmezése: **nincs szenzorlefedettség = nincs mozgás abba
az irányba**. Ezért:

- egy szektor, ami hiányzik a tömbből, `unobserved` — soha nem szabad tér;
- `unobserved` és `clear` **nem keverhető**: a séma elutasítja a `distance_m`-et
  nem `measured` szektorban, mert az meghívná a `max_range_m` akadályként való
  olvasását;
- a `min_range_m` alatti akadály `unobserved`, nem `clear`.

A szektor-bearingek a **vázhoz** képest értendők (`body_frd`), nem északhoz, így
egy yaw-hiba nem forgathatja el az akadálytérképet.

## Amit ez a szerződés nem tud megfogni

Az akkumulátor `remaining_percent` 0–100 skálán van. Ha egy producer a MAVSDK
0–100-as értékét 0–1 törtként olvassa, `0.875` érkezik — ami **érvényes** 0,875%.
Tartományellenőrzés ezt soha nem fogja meg; ez producer-oldali hiba. A szerződés
csak annyit tehet, hogy a skálát egyértelműen rögzíti — ezért van a mező
leírásában. Ez a hiba egyszer már megtörtént, és csendben kikapcsolta az
akku-watchdogot.

## Verziókezelés

A `contract_version` `const: "v0.1"`. Egy jövőbeli verziójú dokumentumot a mai
fogyasztó **elutasít**, nem pedig megpróbál értelmezni. A mezők jelentése a
verzióhoz kötött; új mező vagy szigorítás új verziót igényel.
