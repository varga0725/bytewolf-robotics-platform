# Fejlesztői környezet

## Elsődleges környezet

A PX4 SITL és a Gazebo Harmonic elsődlegesen natív Apple Silicon macOS-en fut.
Az alapértelmezett indítás a PX4 beépített `default` világát és az X500 modellt
használja. Más telepített világ választható a `PX4_GZ_WORLD` környezeti
változóval.

## PX4 forrás

A PX4 forrás fizikai helye szóközmentes útvonalon van, mert egy PX4-alprojekt
nem kezeli helyesen a szóközöket a build-útvonalban:

`~/bytewolf-robotics/PX4-Autopilot`

A projektmappában látható `PX4-Autopilot` erre mutató hivatkozás.

## Indítás

```zsh
cd "/Users/vargaferenc/Documents/ByteWolf Robotics Platform"
./simulation/gazebo/launch/validate_px4_gazebo.zsh
./simulation/gazebo/launch/run_px4_gazebo.zsh base
```

A launcher az `PX4_ROOT` változóval más PX4 checkoutot is fogad. Alapértelmezésben
a projekt `PX4-Autopilot` hivatkozását használja, majd annak szóközmentes fizikai
célútvonalára oldja fel. A `shared/config/x500v2/twin.yaml`
tartja a verziózott profil- és safety-baseline-t; a még nem mért fizikai paraméterek
szándékosan `null` értékűek.

Elérhető profilok: `base`, `vision`, `depth`, `mono-front`, `mono-down`,
`lidar-down`, `lidar-front`, `lidar-2d`.

## Linux VM

Az Ubuntu/UTM virtuális gép nincs törölve, de a 3D szimulátorhoz nem szükséges.
Később ROS 2-specifikus fejlesztéshez használható.

## Vizuális P1 ellenőrzés

A helyi, csak olvasható dashboard replay, a látható Gazebo SITL repülések és a
külön Ubuntu ROS 2 Humble smoke pontos lépései a
[`visual-simulation-verification.md`](visual-simulation-verification.md)
útmutatóban vannak. A dashboard nem repülésvezérlő felület; az élő MAVSDK → ROS
életciklus csak a külön P1 integrációs belépési pont elkészülte után tesztelhető.

## Automatizált és integrációs ellenőrzések

A tesztek a projekt `.venv` környezetében futnak; a `requirements.txt` telepíti
a MissionSpec-, perception- és telemetry-sémákhoz szükséges `jsonschema`
függőséget. Ha az aktuális `python3` nem tartalmaz `venv` modult, teljes CPython
interpreterrel hozd létre a környezetet, például:

```zsh
/opt/homebrew/bin/python3.13 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

A következő automatizált tesztekhez nem kell PX4 SITL: fake MAVSDK/PX4
együttműködőkkel futnak, így a mission- és adapterviselkedést ellenőrzik.

```zsh
cd "/Users/vargaferenc/Documents/ByteWolf Robotics Platform"
.venv/bin/python -m unittest discover -s tests -v
```

PX4 SITL + Gazebo elindítása után a következő parancsok külön, kézi integrációs
ellenőrzések; a jelenlegi tesztcsomag nem indít headless SITL-regressziót:

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land
.venv/bin/python -m brain.cli.fly_waypoint_land
.venv/bin/python -m brain.cli.fly_return_to_home
```

Minden connected flight CLI kötelezően külön, append-only, csak olvasható
telemetria-előzményt ír a mission artifact könyvtárán belül. Ez nem vezérlési
bemenet és offline replayhez használható. A `--telemetry-history` csak a
kötelező history célhelyének explicit megadására való:

```zsh
.venv/bin/python -m brain.cli.fly_takeoff_hover_land \
  --telemetry-history var/mission-runs/takeoff-telemetry.jsonl
```

A waypoint-küldetés GPS-telemetriával igazolja a célba érkezést. A Return-to-Home
küldetés a PX4 saját RTL módját használja. Sikeres futásnál az `in_air` telemetria
előbb repülést, majd leszállást jelez; timeout vagy RTL-hiba esetén az adapter
külön land parancsot kísérel meg, a küldetés pedig hibásként zárul. Mindkét
küldetés a determinisztikus SafetyGate-en halad át, mielőtt parancs kerülne a
PX4-hez. A waypoint közbeni érvénytelen GPS-minta (hiányzó, nem véges vagy
tartományon kívüli koordináta) nem válhat új navigációs paranccsá; levegőben a
futás egyetlen korlátozott land fallbackkel zárul.

Minden CLI-küldetés `v0.2` immutábilis JSON audit-artifactot ír. A fájl a
safety-döntést, a végkimenetelt, az esetleges hibaokot, az állapotátmeneteket és
az arm előtti navigation/home/global-position/battery telemetria-snapshotot
tartalmazza. Tesztfutáshoz adj meg külön könyvtárat, például
`--artifact-dir simulation/artifacts/manual`.

## Headless P0 regressziós mátrix

```zsh
.venv/bin/python -m simulation.scenarios.scenarios
```

A runner izolált PX4/Gazebo process groupot indít, lefuttatja a takeoff-hover-
land, waypoint és RTL scenario-t, valamint a tiltott magasság elutasítását.
Minden scenario saját `mission-artifacts/<scenario>` könyvtárat kap; az útvonal
a `simulation/artifacts/headless/p0-*.json` riportban is szerepel. A timeoutolt
folyamatcsoport garantáltan leáll. A 9/10 ismételhetőségi méréshez több, megőrzött
riport szükséges; ez külön elfogadási mérés, nem az egyszeri runner smoke test.

A headless launcher a már lefordított `px4_sitl_default/bin/px4` binárist PX4
daemon módban (`-d`) indítja. Ez szándékos: a nem olvasott kimeneti csőbe írt
interaktív PX4 prompt korábban meg tudta állítani a SITL-t még a MAVLink
kapcsolat létrejötte előtt. A launcher ezért hibával leáll, ha a SITL bináris
hiányzik; egyszeri felépítéshez a PX4 checkoutban futtasd a
`make px4_sitl gz_x500` parancsot.

Az ismételhetőségi runner egy rövid, egyszeri újrapróbálást végez, ha a macOS
átmeneti folyamatindítási hibával nem engedi létrehozni az izolált SITL sessiont.
Ha a második indítás is hibás, a kör fail-closed, blokkolt riporttal zárul.

A kapu automatizált mérése:

```zsh
.venv/bin/python -m simulation.scenarios.scenarios --runs 10
```

## P0.v2 izolált mátrix és bizonyítási szintek

Az új forgatókönyveket külön verzióval futtasd:

```zsh
.venv/bin/python -m simulation.scenarios.scenarios --matrix-version p0.v2
```

A P0.v2 MAVSDK-s forgatókönyvei egymástól független PX4/Gazebo lifecycle-ben
indulnak. Az artifact alapesetben `app+SITL` bizonyíték.

A low battery ennél erősebb: `PX4/Gazebo fault-injection`. A mátrix a valódi akkut
meríti le a tartalék alá lebegés közben (`SIM_BAT_DRAIN`, `SIM_BAT_MIN_PCT`), és a
riport rögzíti, hogy a PX4 milyen értéket erősített meg. A PX4 csak armolt állapotban
merít és disarmnál 100%-ra állít vissza, ezért az **armolási** tartalék így nem érhető el.

A repülés közbeni GNSS-invalid és a telemetria-kiesés `unit/contract` szinten marad:
a PX4 `SIM_GZ_EN_GPS` paramétere `reboot_required`, tehát a GNSS menet közben nem vehető el.
Leállt MAVSDK kliensnél nem alkalmazásoldali LAND történik, hanem a PX4-failsafe
felelőssége — ez nem injektálható, és nem is állítunk róla SITL-bizonyítékot.

A MAVSDK `remaining_percent` mezője **0–100** skálán jön. Soha ne skálázd át: amikor a kód
0–1 törtként olvasta, minden érték érvénytelennek látszott, amit az
`allow_missing_battery_telemetry` elnyelt, és ezzel némán kikapcsolta az armolási akku-kaput
és a repülés közbeni akku-figyelést is.

A fix 3, 6 és 10 m/s szél-fixture-öket a P0.v2 mátrix maga építi fel és rögzíti
a riportban; kézi előkészítés nem kell. Kézi vizsgálathoz a pontos parancsot a
fő README `Run the expanded P0.v2 matrix` része tartalmazza.

Egy szél-fixture három részből áll, és mindhárom kötelező: a világ (`PX4_GZ_WORLD_FILE`),
a szélre reagáló váz (`PX4_GZ_MODELS`) és a szélrendszert betöltő server config
(`PX4_GZ_SERVER_CONFIG`). Önmagában a szeles világ semmit nem bizonyít: a Gazebo
csak azokra a linkekre fejt ki szelet, amelyek `enable_wind`-del kérik, és csak akkor,
ha a `WindEffects` rendszer be van töltve — a PX4 gyári X500-a egyiket sem teljesíti.
A szélerő a twin `aerodynamics` légellenállásához van skálázva; a Gazebo 1.0-s
alapértéke nem légellenállás-modell, hanem szélsebességre gyorsítja a gépet.

Csak a fixture-t ténylegesen betöltő PX4/Gazebo report jelölhető wind-evidence-nek.
A 10 m/s-os futás emellett jelzi, hogy a drag modell 2–9 m/s-os validált sávján kívül
extrapolál.

Apple Silicon nightly vagy kézi regressziós belépési pont:

```zsh
./simulation/gazebo/launch/run_p0_nightly.zsh
```

A mátrix a nominális repülések mellett valós SITL waypoint-timeout → egyszeri
land fallback és elérhetetlen MAVLink-végpont → arm előtti fail-closed linkhiba
scenariót is futtat.

A `p0-repeatability-*.json` összesítő minden nominális scenario külön
sikerarányát tartalmazza. Takeoff, waypoint és RTL egyaránt legalább 0,9 arányt
kell elérjen; a tiltott magasság biztonsági elutasításának minden körben sikeres
scenario-ként kell zárulnia.
