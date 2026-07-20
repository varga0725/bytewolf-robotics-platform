# Dashboard alkalmazás

Helyi, csak olvasható telemetriai nézet. Nincsenek vezérlő végpontok vagy
drón-parancsok. A telemetria egy JSON-fájlból olvasható, ezért a későbbi ROS 2
bridge ugyanarra az alakra írhat adatot.

## Oldalak

A `apps/api/server` alatt futó teljes felület bal oldali sávból öt oldalra bomlik:

| Oldal | Tartalom |
| --- | --- |
| Állapot | telemetria-metrikák és a munkamenet küldetés-eseményei |
| Kamera | élő kamerakép észlelés-overlayjel, első/alsó váltással |
| Chat | beszélgetés, terv-jóváhagyás, és a memóriafrissítés státusza |
| Memória | a személyes tények listája (javítás/törlés) és a „rólad kialakított kép" gráf |
| Világ | akadálytérkép, bizonyíték-gráf és a világ-állítások listája |

A Memória és a Világ oldal **szándékosan külön** van, és a két gráf között nincs
él: a személyes memória és a világ-bizonyíték két külön tároló, egy észlelésből
soha nem lesz személyhez kötött tény. Részletek: `docs/world-memory-v0_1.md`.

```sh
python -m apps.dashboard.server \
  --telemetry-file apps/dashboard/examples/sitl-replay.json
```

Ezután nyisd meg: `http://127.0.0.1:8080`.

Példa JSON:

```json
{
  "position": {"latitude_deg": 47.4979, "longitude_deg": 19.0402, "absolute_altitude_m": 125.5},
  "battery": {"remaining_percent": 78.5},
  "in_air": true
}
```

Az aktuális mission-artifactok `telemetry.battery_percent` mezőjét is képes
megjeleníteni; hiányzó pozíciót vagy repülési állapotot nem talál ki.

Az `examples/sitl-replay.json` csak vizuális replay minta. A timestampje a
futtatás napján várhatóan `STALE` lesz; ez szándékos, és az állapotjelző
helyes működését mutatja.
