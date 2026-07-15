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
