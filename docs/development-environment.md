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
./simulation/launch/validate_px4_gazebo.zsh
./simulation/launch/run_px4_gazebo.zsh base
```

A launcher az `PX4_ROOT` változóval más PX4 checkoutot is fogad. Alapértelmezésben
a projekt `PX4-Autopilot` hivatkozását használja, majd annak szóközmentes fizikai
célútvonalára oldja fel. A `platforms/x500v2/config/twin.yaml`
tartja a verziózott profil- és safety-baseline-t; a még nem mért fizikai paraméterek
szándékosan `null` értékűek.

Elérhető profilok: `base`, `vision`, `depth`, `mono-front`, `mono-down`,
`lidar-down`, `lidar-front`, `lidar-2d`.

## Linux VM

Az Ubuntu/UTM virtuális gép nincs törölve, de a 3D szimulátorhoz nem szükséges.
Később ROS 2-specifikus fejlesztéshez használható.

## Automatizált és integrációs ellenőrzések

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

A waypoint-küldetés GPS-telemetriával igazolja a célba érkezést. A Return-to-Home
küldetés a PX4 saját RTL módját használja. Sikeres futásnál az `in_air` telemetria
előbb repülést, majd leszállást jelez; timeout vagy RTL-hiba esetén az adapter
külön land parancsot kísérel meg, a küldetés pedig hibásként zárul. Mindkét
küldetés a determinisztikus SafetyGate-en halad át, mielőtt parancs kerülne a
PX4-hez.
