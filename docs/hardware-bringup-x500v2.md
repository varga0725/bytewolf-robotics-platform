# Fizikai hardver bring-up — X500 V2 dev kit + Hawkeye 4K Split V5

Ez a dokumentum azt írja le, hogyan lehet a **most beszerzett fizikai hardvert**
tesztelni és a digital-twinnel összemérni **akkumulátor és companion computer
(Jetson) nélkül**, és mit blokkol a hiányuk. A twin szigorú provenance-szabályát
követi: vendor-adat nem mérés, és a `twin.yaml` egyetlen `measured` mezője sem
töltődik, amíg a tényleges bench-mérés meg nem történt.

## Beszerzett hardver

| Eszköz | Mi ez | Forrás |
| --- | --- | --- |
| Holybro X500 V2 developer kit | Pixhawk 6C FC, M8N/M10 GPS, SiK telemetry rádió, előszerelt motor+ESC karok, PDB, propellerek | [Holybro docs](https://docs.holybro.com/drone-development-kit/px4-development-kit-x500v2) |
| Hawkeye 4K Split V5 | 1/2.3" 12 MP CMOS, natív 4K30, **FOV 160°**, 19 g, USB-UVC / HDMI / AV, gyro EIS | [Makerfire](https://shop.makerfire.com/products/hawkeye-4k-split-v5-camera) |

Mindkettő rögzítve a `shared/config/x500v2/twin.yaml` új `hardware:` szekciójában
(`hardware.acquired`), vendor provenance-szal.

### Még hiányzó (blokkoló) hardver

- **Repülési akku** (4S ~5000 mAh ajánlott) — nélküle nincs motoros/repülési teszt,
  nincs propulsion- és battery-mérés, a `vehicle.mass_kg` csak száraz tömegként mérhető.
- **Companion computer** (Jetson / Raspberry Pi 4) — nélküle nincs valós perception
  pipeline és nincs kamera end-to-end latency mérés. A platform-lap furatai ehhez
  előre készültek.

## Amit MOST el lehet végezni (akku és Jetson nélkül)

Minden itteni mérés egy konkrét, ma `null` twin-paramétert céloz. A megfelelő
`plan` mezők a `twin.yaml`-ben és a `hardware.bench_measurable_now` lista is erre mutat.

### 1. Flight controller bench bring-up (Pixhawk 6C, USB-tápról)

Cél: a FC él, a firmware a baseline, a szenzorok kalibráltak. **Propeller nélkül,
akku nélkül.** A Pixhawk USB-C-ről táplálható és teljesen konfigurálható.

1. Csatlakoztasd a Pixhawk 6C-t USB-C-vel a laptophoz, indíts QGroundControl-t.
2. Ellenőrizd/flasheld a **PX4 v1.17.0** firmware-t — ez a projekt baseline
   (`twin.yaml: simulation.px4_version`, `simulation/px4/macos-build.patch`).
3. Airframe: **Holybro X500 V2 (Quad X)**.
4. Szenzor-kalibráció: accelerometer, gyroscope, magnetometer/compass, szintezés (level horizon).
5. GPS: kültéren ellenőrizd a fix-et (M8N/M10), figyeld a HDOP/sat count értéket.

**Elfogadási kritérium:** minden kalibráció „OK", a FC arm-preflightot csak a
várt hiányzó feltételek (pl. nincs akku, nincs távadó) blokkolják.

### 2. IMU / barométer zajmérés (Allan variance) → twin null-ok feloldása

A `sensors.imu.gyro_noise_density`, `sensors.imu.accel_noise_density` és a
`sensors.barometer.noise_pa` ma `null`. A provenance-tervük pontosan egy statikus
bench-recordinget ír elő a **ténylegesen megvett FC-ről** — ez most a Pixhawk 6C,
és USB-tápról elvégezhető.

1. A FC-t rögzítsd rezgésmentesen, hagyd bemelegedni, majd rögzíts hosszú (10–30 perc)
   statikus IMU/baro logot (PX4 ULog).
2. Számold az Allan-variance görbéket (gyro/accel noise density, bias instability), baro zaj.
3. Az eredményt **measured** provenance-szal írd a `twin.yaml`-be — de csak a
   tényleges mérés után (lásd a twin scope döntést: most csak vendor-tények + tervek).

### 3. Airframe tömeg és inercia (mechanikai, táp nélkül)

- **Száraz tömeg:** mérd le az összeszerelt vázat (frame + FC + motorok + ESC-k +
  kamera) akku nélkül. Ez a `vehicle.mass_kg` részleges feloldása; a repülési tömeg
  az akku beszerzésekor egészül ki. Jelöld egyértelműen „dry mass"-ként.
- **Inercia (ixx/iyy/izz):** bifilar-inga méréssel az összeszerelt vázon. Mechanikai,
  nem igényel tápot. Feloldja a `vehicle.inertia_kg_m2.*` null-okat.

### 4. Kamera bench + kalibráció (Hawkeye 4K Split V5)

A Hawkeye USB-UVC-n közvetlenül a laptopra köthető (UVC webkameraként), vagy
HDMI-monitorra / SD-kártyára rögzít — **Jetson nélkül is** megnézhető a nyers kép.

- Ellenőrizd a valós felbontást, FPS-t és FOV-t.
- **Kamerakalibráció** (intrinsics + fisheye-torzítás) — a 160°-os halszem miatt a
  valós hasznosításhoz kötelező. A kalibrációs pipeline készen áll a repóban:

**Kalibrációs protokoll (fisheye):**

1. Kösd a Hawkeye-t UVC-n a laptopra, 4K módban.
2. Rögzíts ~20 pózt egy checkerboard (pl. 9×6, 25 mm) mintáról a látómező széleit is
   lefedve — a fisheye torzítás a széleken a legerősebb.
3. Futtasd a standard **OpenCV fisheye** kalibrációt (`cv2.fisheye.calibrate`) — ez a
   `K` mátrixot (fx, fy, cx, cy), a `D` torzítási együtthatókat (Kannala-Brandt, 4 db)
   és az RMS reprojection error-t adja.
4. Írd az eredményt a kalibrációs kontraktba:
   `shared/schemas/perception/camera_calibration_v0_1.schema.json` (measured provenance,
   `camera_id: front_rgb`, `projection_model: fisheye`).
5. A `brain.perception.camera_calibration.load_camera_calibration()` **fail-closed**
   validálja: séma, véges értékek, képen belüli principal point, és a reprojection error
   a bizalmi küszöb (1,5 px) alatt. Csak ezután tekinthető a kamera kalibráltnak; a twin
   `front_rgb.calibration_status` ekkor válik `uncalibrated`-ről `calibrated`-re.

> A mérés a te bench-lépésed; a repo a mért érték szigorú, validált beolvasása és a
> perception-be kötése. A kód **nem** talál ki kalibrációs számot — measured provenance
> csak tényleges checkerboard-mérésből.

**Nem mérhető most:** a kamera **end-to-end latency** (`sensors.cameras.front_rgb.latency_ms`)
a teljes capture→availability láncot igényli a companion computeren — Jetson kell hozzá.

## Amit NEM lehet elvégezni akku/Jetson nélkül

| Blokkolt | Miért | Feloldó hardver |
| --- | --- | --- |
| Motor/propulsion konstansok (`propulsion.*`) | ESC-hez teljesítmény kell; a motorpad thrust/torque görbéhez táp | akku vagy labor-tápegység |
| Tényleges repülés / hover / SITL-parity flight | motoros működés kell | akku |
| Battery paraméterek (`battery.*`) | nincs fizikai akku | akku |
| Kamera end-to-end latency | teljes feldolgozási lánc kell | companion computer (Jetson) |
| Valós perception pipeline a vason | UVC stream feldolgozás kell | companion computer (Jetson) |

## Digital-twin igazítás — mi történt és mi vár

A twin scope döntés: **csak vendor-tények + mérési tervek**, measured-et csak
tényleges bench-mérés után.

- **Rögzítve most:** a beszerzett eszközök vendor-adatai (`twin.yaml → hardware.acquired`);
  a hiányzó hardver és amit blokkol (`hardware.not_yet_acquired`); a most elvégezhető
  bench-mérések (`hardware.bench_measurable_now`); a mérési tervek pontosítása ott, ahol
  a hardver egy része már kézben van (IMU/baro, tömeg, front kamera latency provenance).
- **`front_rgb` deklaráció igazítva a Hawkeye-hez:** a twin `sensors.cameras.front_rgb`
  most a valós geometriát hordozza — **4K (3840×2160), 2.793 rad (160°), `projection: fisheye`**,
  nem a PX4 gyári 1080p / 1.74 rad-ját. A `down_rgb` precíziós-landing slot érintetlen.
- **Két őszinte, rögzített fenntartás (`hardware.sim_vs_hardware_gaps`):**
  (1) a Gazebo `<camera>` **pinhole** modell, a 2.793 rad rendering egy fisheye
  wide-pinhole közelítése — a valós fisheye torzítást a **mért kalibráció** adja;
  (2) a Gazebo front-overlay a FOV-t még nem köti be. A render-képesség kész
  (`simulation/gazebo/camera_profiles.py`: `render_camera_horizontal_fov`, `declared_camera_fov`),
  a bekötés + SITL-igazolás a front kamera engedélyezésekor esedékes (ma `enabled: false`).
- **Kalibrációs kontrakt kész:** `shared/schemas/perception/camera_calibration_v0_1.schema.json`
  + `brain/perception/camera_calibration.py` (fail-closed loader). A `pinhole_intrinsics()`
  **elutasítja** a nyers fisheye-t, így a 160°-os lencse nem juthat a pinhole
  `target_estimator`-be undistort nélkül.

## Bring-up ellenőrzőlista

- [ ] Pixhawk 6C USB bring-up + PX4 v1.17.0 + szenzor-kalibráció (QGroundControl)
- [ ] IMU/baro Allan-variance recording → `twin.yaml` measured (mérés után)
- [ ] Airframe száraz tömeg + inercia → `twin.yaml` measured (mérés után)
- [ ] Kültéri GPS-fix scatter (M8N/M10) → `sensors.gps.*_noise_m`
- [ ] Hawkeye UVC bench + kamerakalibráció (intrinsics + fisheye)
- [ ] (Akku beszerzése után) propulsion bench + repülési tömeg + hover
- [ ] (Jetson beszerzése után) kamera end-to-end latency + valós perception pipeline
