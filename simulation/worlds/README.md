# Worlds

A projekt saját Gazebo world-assetjeinek helye. A PX4 beépített világai a külső
PX4 checkoutban maradnak; a szél-fixture-öket ezekből rendereli a
`simulation/gazebo/wind_profiles.py`, nem itt élnek.

## Budapest — Árpádföld/Mátyásföld

Az első ByteWolf városi jelenet: alacsony poligonszámú épülettömegek és főutak
valós budapesti koordinátákon, elég könnyen ahhoz, hogy PX4 SITL alatt fusson.

Amit commitolunk, az a **forrás, nem a termék**:

- `build_budapest_world.py` — a generátor.
- `data/budapest_arpadfold_matyasfold.osm.gz` — az OpenStreetMap-pillanatkép,
  amiből épül, gzippelve (7,5 MB XML → 998 KB).

Maga a world generált és git-ignorált. A pillanatkép az, amitől a jelenet
egyáltalán reprodukálható: az Overpass azt szolgálja ki, ami az OSM-ben *ma* van,
így a `--refresh` egy **másik** várost épít, mint amiben a korábbi repülések
történtek. A pillanatképből való építés nem igényel hálózatot és tanúsítványt.

```zsh
# A world újragenerálása (2617 épület, 763 útszakasz) a
# simulation/worlds/generated/ könyvtárba.
.venv/bin/python simulation/worlds/build_budapest_world.py
```

### Repülés benne

A headless launcher közvetlenül fogad world-fájlt:

```zsh
PX4_GZ_WORLD=budapest_arpadfold_matyasfold \
  PX4_GZ_WORLD_FILE="$PWD/simulation/worlds/generated/budapest_arpadfold_matyasfold.sdf" \
  ./simulation/gazebo/launch/run_px4_gazebo_headless.zsh base
```

A látható launcher a worldöt névvel adja át a PX4-nek, az pedig kizárólag a saját
worlds könyvtárában keresi — oda kell tehát generálni. Az a példány eldobható
build-termék: soha ne szerkeszd, és ne tekintsd forrásnak.

```zsh
.venv/bin/python simulation/worlds/build_budapest_world.py \
  --output "$PWD/PX4-Autopilot/Tools/simulation/gz/worlds/budapest_arpadfold_matyasfold.sdf"
PX4_GZ_WORLD=budapest_arpadfold_matyasfold ./simulation/gazebo/launch/run_px4_gazebo.zsh base
```

### Attribúció

A jelenet OpenStreetMap-adatokból származik, © OpenStreetMap contributors, az
Open Database License (ODbL) feltételei szerint.
