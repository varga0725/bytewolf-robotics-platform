# P1 vizuális szimulációs ellenőrzés

Ez az útmutató a P1 telemetriai fejlesztések jelenlegi, bizonyítható állapotát írja le. A dashboard szigorúan csak olvasható: nincs benne arm, takeoff, navigáció, RTL, land vagy más vezérlő végpont. A repülési parancsok továbbra is az elkülönített MAVSDK CLI-kből indulnak, a meglévő safety gate-en keresztül.

## Gyors állapot

| Ellenőrzés | Hol fut | Mit igazol | Mit nem igazol |
| --- | --- | --- | --- |
| Dashboard replay | natív macOS | a böngészős, helyi olvasási nézet és az adatfrissesség-jelzés | élő PX4/MAVSDK kapcsolat |
| Látható PX4 SITL + Gazebo | natív macOS | az X500 mozgása és a meglévő P0 repülési CLI-k | ROS 2 adatfolyam |
| MAVSDK relay egységteszt | natív macOS | a dashboard három core adatfolyamának atomikus JSON-pillanatképpé válását és a kötelező history-state streamek validálását | valódi MAVSDK/PX4 kapcsolat |
| Élő ROS 2 bridge smoke | Ubuntu + ROS 2 Humble | MAVSDK → ROS telemetry → JSON-pillanatkép életciklusát | dashboard-vezérlést (ilyen nincs) |

## 1. Dashboard replay macOS-en

Egy terminálban, a projekt gyökeréből indítsd el a helyi nézetet:

```zsh
.venv/bin/python -m apps.dashboard.server \
  --telemetry-file apps/dashboard/examples/sitl-replay.json
```

Nyisd meg a böngészőben: `http://127.0.0.1:8080`.

Ellenőrizd, hogy megjelenik a pozíció, akkumulátor és repülési állapot. A mintafájl rögzített időbélyege miatt a státusz várhatóan `STALE`; ez helyes viselkedés, nem hiba. A `/api/telemetry` ugyanazt az állapotot JSON-ként, szintén csak olvashatóan mutatja. Egy `POST` kérésnek `405 Read-only dashboard` válasszal kell elutasítódnia.

Leállítás: `Ctrl-C`. A szerver kizárólag a `127.0.0.1` címen figyel, tehát nem teszi elérhetővé a nézetet a helyi hálózaton.

## 2. X500 vizuális SITL/Gazebo ellenőrzés macOS-en

Az első terminálban indítsd a grafikus szimulátort:

```zsh
./simulation/gazebo/launch/validate_px4_gazebo.zsh
./simulation/gazebo/launch/run_px4_gazebo.zsh base
```

Várd meg, amíg a Gazebo ablakban az X500 stabilan a talajon áll. Ezután egy második terminálban futtatható például a rövid, látható fel- és leszállás:

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land \
  --altitude 1 --hover-seconds 10
```

Waypoint és RTL szemrevételezéshez ugyanebben a második terminálban:

```zsh
.venv/bin/python -m brain.cli.fly_waypoint_land \
  --takeoff-altitude 2 --north 5 --east 0 --waypoint-altitude 2 --hover-seconds 3

.venv/bin/python -m brain.cli.fly_return_to_home \
  --takeoff-altitude 2 --hover-seconds 3
```

Minden parancs után várd meg a `Mission completed` sort, és a Gazebóban is ellenőrizd a felszállást, mozgást/RTL-t és földet érést. Egy időben csak egy MAVSDK-alapú CLI legyen aktív az UDP és MAVSDK-port ütközések elkerülésére. Leállításhoz először fejezd be a küldetést, majd az első terminálban `Ctrl-C`.

## 3. Mit lehet most együtt, vizuálisan ellenőrizni?

A dashboard replay és a grafikus SITL egyidejűleg is futhat, de jelenleg **nem ugyanazt az élő adatfolyamot** mutatják: a replay szándékosan a rögzített `sitl-replay.json` fájlt olvassa, míg a Gazebo a PX4 szimulációt jeleníti meg. Ezért a replay csak a dashboard megjelenítési határát ellenőrzi, és nem szabad élő dróntelemetriának tekinteni.

Az élő MAVSDK → ROS → JSON relay futtatható belépési pontja
`brain.cli.ros2_telemetry_bridge`, de ROS 2 Humble-t igényel, ezért ezen a
macOS-en nem indítható. A relay a dashboard számára továbbra is a három core
MAVSDK állapotból (position, battery, in-air) ír atomikus JSON pillanatképet,
de a repülési CLI-k kötelező history-rögzítése ennél több, validált state
streamet is elmenthet (például velocity, attitude, IMU, battery diagnostics,
ground truth és local position/velocity), ha az adapter ezeket ténylegesen
szolgáltatja. A relay továbbra sem hív PX4 flight-control API-t. A macOS
dashboardot továbbra is a replay fájllal ellenőrizd; egy repülési CLI artifactja
nem élő relay formátum.

Az eddigi határtesztek futtatása:

```zsh
.venv/bin/python -m unittest discover -s tests -v
```

## 4. Külső Ubuntu + ROS 2 Humble élő smoke (nem macOS-feladat)

Ezen a macOS fejlesztői gépen a `ros2` parancs és a Python `rclpy` modul nincs telepítve. Ez szándékos: a ROS adapter opcionális, és a P0/P1 macOS SITL útvonalat nem blokkolhatja. A következő lépések kizárólag az Ubuntu VM-ben, ROS 2 Humble környezetben végezhetők el.

Az itteni `python3` szándékosan a ROS 2 rendszer-Pythonja, nem a projekt `.venv`-je: az `rclpy` csak abban érhető el. A bridge viszont a projekt függőségeit (`mavsdk`) is igényli, tehát ennek a Pythonnak mindkettőt látnia kell — például `python3 -m venv --system-site-packages` környezettel. **Ez a szakasz még soha nem futott le**: környezet híján a P1 Ubuntu smoke halasztva van, így a pontos telepítési lépés akkor rögzül, amikor a környezet elkészül. Addig ez terv, nem igazolt eljárás.

```bash
source /opt/ros/humble/setup.bash
cd /path/to/ByteWolf-Robotics-Platform
python3 -c 'import rclpy; rclpy.init(); from robots.drone.x500v2.ros2.telemetry_adapter import create_ros2_telemetry_node; node = create_ros2_telemetry_node(); print(node.node.get_name()); node.destroy_node(); rclpy.shutdown()'
```

Ennek a parancsnak a `bytewolf_x500v2_telemetry` node-nevet kell kiírnia. Ez csak a publisher node létrehozását igazolja; nem indít PX4-et, MAVSDK-t vagy repülési parancsot.

Ubuntu SITL/Gazebo indítása után egy terminálban indítsd az élő, csak
telemetriai bridge-et:

```bash
source /opt/ros/humble/setup.bash
python3 -m brain.cli.ros2_telemetry_bridge \
  --endpoint udpin://0.0.0.0:14540 \
  --dashboard-snapshot simulation/artifacts/dashboard/live-telemetry.json
```

Egy második terminálban a három, és csak a három szerződéses adatfolyam
ellenőrizhető:

```bash
source /opt/ros/humble/setup.bash
ros2 topic list | grep '^/bytewolf/x500v2_reference_01/telemetry/'
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/position
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/battery
ros2 topic echo --once /bytewolf/x500v2_reference_01/telemetry/flight_state
```

A JSON-pillantkép vizuális ellenőrzéséhez egy harmadik terminálban futtasd:

```bash
python3 -m apps.dashboard.server \
  --telemetry-file simulation/artifacts/dashboard/live-telemetry.json
```

A böngészőben megjelenő `LIVE` státusz csak akkor várható, ha mindhárom
telemetria-forrás érkezik. Leállításkor a bridge terminálban adj `Ctrl-C`-t;
a bridge szabályosan bezárja a ROS node-ot, a ROS contextet és a saját MAVSDK
szerverét. A szerződéses topicok pontos nevei:
`/bytewolf/x500v2_reference_01/telemetry/position`,
`/bytewolf/x500v2_reference_01/telemetry/battery` és
`/bytewolf/x500v2_reference_01/telemetry/flight_state`. Más topic és minden
vezérlési felület tiltott.

## Biztonsági határ

Ez az útmutató szimulációs ellenőrzésre szolgál. Sem a replay, sem a ROS node, sem a dashboard nem minősíti a fizikai drónt repülésre. Fizikai X500 V2 előtt külön hardver-, failsafe-, GPS- és helyszíni biztonsági validáció szükséges.
