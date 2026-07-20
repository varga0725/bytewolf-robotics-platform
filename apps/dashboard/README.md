# Dashboard alkalmazás

Helyi, csak olvasható telemetriai nézet. Nincsenek vezérlő végpontok vagy
drón-parancsok. A telemetria egy JSON-fájlból olvasható, ezért a későbbi ROS 2
bridge ugyanarra az alakra írhat adatot.

## Mire van szükség ahhoz, hogy éljen

| Amit látni akarsz | Mi írja | Ha nem fut |
| --- | --- | --- |
| Telemetria, kamera, chat | `brain.cli.dashboard_telemetry` (vagy egy futó küldetés-CLI) | a snapshot a legutóbbi küldetésé marad, és a felület kapcsolat nélkülinek látszik |
| Világtérkép, akadálycellák | `simulation.perception.survey_recorder` vagy az obstacle scenario | üres marad — **lidar nélküli airframe-en soha nem lesz cella** |
| Küldetés-térkép háttérképe | `simulation.gazebo.map_view` | a térkép üres koordináta-rendszer marad, és vakon kell célpontot kijelölni |
| Küldetés indítása | `apps.api.server` + explicit jóváhagyás | a dashboard csak olvas |

A `base` (`gz_x500`) airframe-en **nincs lidar**, ezért térkép sem keletkezhet;
ehhez `lidar-2d` (`gz_x500_lidar_2d`) kell.

## A küldetés-térkép háttérképe

```sh
python -m simulation.gazebo.map_view
```

Ez egy statikus, lefelé néző kamerát helyez a **futó** Gazebo világba a vehicle
spawn pontja fölé, és onnan renderel felülnézetet a küldetés-térkép alá, a saját
méretarányával együtt (`map-view.json`). PX4-módosítást és újraindítást nem
igényel.

A világ jelenetét viszont megváltoztatja: **bizonyíték-futás alatt nem futhat.**
Egy scenario- vagy baseline-futás világában nem lehet benne ez a kamera, mert egy
befecskendezett modelltől a futás már nem az a világ, amit a `baseline.yaml`
rögzít. A modell neve ezért beszédes: `bytewolf_map_camera`.

A kép tájolása mért, nem feltételezett: a nyers renderben Kelet van fent és Észak
balra, ezért a modul negyed fordulattal forgatja észak-fel állásba. A méretarány
0,23 m/pixel 158 m magasságból, amit egy 40 m-es kameraeltolás 176 pixeles
képelmozdulása igazol vissza (`tests/test_map_view.py`).

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
