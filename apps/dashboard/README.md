# Dashboard alkalmazás

Helyi, csak olvasható telemetriai nézet. Nincsenek vezérlő végpontok vagy
drón-parancsok. A telemetria egy JSON-fájlból olvasható, ezért a későbbi ROS 2
bridge ugyanarra az alakra írhat adatot.

## Mire van szükség ahhoz, hogy éljen

| Amit látni akarsz | Mi írja | Ha nem fut |
| --- | --- | --- |
| Telemetria, chat | `brain.cli.dashboard_telemetry` (vagy egy futó küldetés-CLI) | a snapshot a legutóbbi küldetésé marad, és a felület kapcsolat nélkülinek látszik |
| Élő kamerakép | `simulation.perception.camera_stream` | a kamerapanel a stream-re vár |
| Világtérkép, akadálycellák | `simulation.perception.survey_recorder` vagy az obstacle scenario | üres marad — **lidar nélküli airframe-en soha nem lesz cella** |
| Küldetés-térkép háttérképe | `simulation.gazebo.map_view` | a térkép üres koordináta-rendszer marad, és vakon kell célpontot kijelölni |
| Küldetés indítása | `apps.api.server` + explicit jóváhagyás | a dashboard csak olvas |

## A PX4-link egyszerre egy folyamaté

A `udpin://0.0.0.0:14540` portot egyszerre csak egy MAVSDK-szerver kötheti. A
telemetria-híd fogja, amíg a szimulátor fut; egy jóváhagyott küldetésnek viszont
szüksége van rá. Ezt egy bérlet-fájl rendezi
(`simulation/artifacts/dashboard/mavlink-link.lease`): a küldetés indulás előtt
bejegyzi magát, a híd erre elengedi a portot, a küldetés vége után pedig a híd
magától visszaveszi. A repülő CLI ugyanabba a snapshotba ír, ezért a dashboard a
váltás alatt is látja a drónt — a böngészőből az egész átadás láthatatlan.

A bérlet semmit nem engedélyez és semmit nem tilt: nem tud armolni, parancsolni
vagy repülést megakadályozni. Csak azt mondja meg, melyik olvasó tartsa a
socketet; a biztonsági hatóság változatlanul a PX4.

Egy összeomlott küldetés bérlete nem marad örökre érvényes: a fájl a birtokló
process azonosítóját is rögzíti, és egy már nem élő processzre hivatkozó bérlet
felszabadítottnak számít.

A `base` (`gz_x500`) airframe-en **nincs lidar**, ezért térkép sem keletkezhet;
ehhez `lidar-2d` (`gz_x500_lidar_2d`) kell.

## A kamerakép 30 fps-en

A kamera azt a felbontást rendereli, amit a `twin.yaml` deklarál (ma 1920×1080),
és a dashboard is ennyit mutat, másodpercenként 30 képkockával. Ehhez három
dolog kellett, mindhárom mérésből:

- **Natív Gazebo-átvitel.** A `gz topic --json-output` base64-be és JSON-ba
  csomagolta minden képkockát — 1080p-n 8 MB szöveg képkockánként, Pythonban
  parse-olva. A `gz-transport` saját Python bindingja (Homebrew site-packages)
  ugyanezt protobuffal adja: 30,3 fps mérve.
- **A detektor a kép mellett fut, nem előtte.** 1080p-n 44 ms képkockánként,
  ami önmagában 22 fps-re fogná a képet. Saját, lassabb ütemen megy (0,2 s),
  bőven a `detections` szerződés 0,5 s frissességi korlátján belül.
- **A böngésző nem kérdez, hanem kap.** `multipart/x-mixed-replace` stream a
  `/api/v1/cameras/{sensor}/stream` végponton. A korábbi 250 ms-os poll 4 fps-re
  vágta a képet, függetlenül attól, mennyit termelt a szimulátor.

Egy apróság, ami sokba került: a képkocka-ütemezés **tűréssel** dolgozik. Pontosan
a forrás ütemét kérve (1/30 s) minden hajszálnyival korán érkező képkocka
kiesett, és a 30 fps 19-re esett.

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
