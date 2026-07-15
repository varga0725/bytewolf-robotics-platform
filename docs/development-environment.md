# Fejlesztői környezet

## Elsődleges környezet

A PX4 SITL és a Gazebo Harmonic elsődlegesen natív Apple Silicon macOS-en fut.
Ez a beállítás ellenőrizve lett X500 modellel és az Árpádföld–Mátyásföld
világgal.

## PX4 forrás

A PX4 forrás fizikai helye szóközmentes útvonalon van, mert egy PX4-alprojekt
nem kezeli helyesen a szóközöket a build-útvonalban:

`~/bytewolf-robotics/PX4-Autopilot`

A projektmappában látható `PX4-Autopilot` erre mutató hivatkozás.

## Indítás

```zsh
cd ~/bytewolf-robotics/PX4-Autopilot
source .venv/bin/activate
PX4_GZ_WORLD=budapest_arpadfold_matyasfold \\
CMAKE_PREFIX_PATH="$(brew --prefix qt@5)" \\
make px4_sitl gz_x500
```

## Linux VM

Az Ubuntu/UTM virtuális gép nincs törölve, de a 3D szimulátorhoz nem szükséges.
Később ROS 2-specifikus fejlesztéshez használható.

## Végponttól végpontig tesztek

A PX4 SITL elindítása után, egy második terminálból:

```zsh
cd "/Users/vargaferenc/Documents/ByteWolf Robotics Platform"
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m brain.cli.fly_waypoint_land
.venv/bin/python -m brain.cli.fly_return_to_home
```

A waypoint-teszt GPS-telemetriával igazolja a célba érkezést. A Return-to-Home
teszt a PX4 saját RTL módját használja, és az `in_air` telemetriával ellenőrzi a
leszállást. Mindkét küldetés a determinisztikus SafetyGate-en halad át, mielőtt
parancs kerülne a PX4-hez.
