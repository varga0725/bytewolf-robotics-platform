# Valós repülés előkészítése — Holybro X500 V2 Dev Kit (PX4)

**Státusz:** DRAFT (terv / dokumentáció, nem elfogadott eljárás, nem repülési engedély)  
**Dátum:** 2026-11-14, Europe/Budapest  
**Célgép:** Holybro PX4 Development Kit — X500 V2 (a kezelő leírása szerint Holybro X500 V2 Dev Kit)  
**Irányítás:** RC adó **nincs és nem is szükséges**. A gép laptopon, QGroundControl + PS5 joystick + telemetria-rádió (MAVLink joystick / `MANUAL_CONTROL`) kombinációval repül; a kezelő ezzel a módszerrel már repült.  
**ByteWolf platform:** a fizikai aktuáció zárja (`physical_actuation` / `_PHYSICAL_ACTUATION_RELEASED`) **zárva marad**. Ez a dokumentum csak előkészítés. Nem utasítás éles FC-re, szerverre vagy repóra való írásra, és nem engedély a zár feloldására.

> Ez a dokumentum **nem** meríti ki a hatósági megfelelést, és **nem** helyettesíti a tényleges hardver átvizsgálását, a telepített PX4 firmware paraméterlistáját, sem a magyar légiközlekedési hatóság aktuális tájékoztatását. Minden állítás forrással van jelölve. Amit a nyilvános forrásokból nem lehetett egyértelműen igazolni, az **ELLENŐRIZENDŐ**.

---

## 0. Hatály, biztonság, terminológia

- Csak terv és checklist. **Senki ne változtasson éles flight controller paramétert, ne armoljon valós motort, és ne repüljön** pusztán e draft alapján.
- Titok, token, hostname, IP, tailnet ID **nem** szerepel a dokumentumban, és nem is kerülhet jegyzőkönyvbe vagy ticketbe.
- A paraméternév- és enum-értékeket a **PX4 v1.17.0** forrásából és a PX4 Guide (v1.17 / main) Safety / Manual Control / Return / Arming oldalairól ellenőriztem. Az éles FC-n **először a telepített firmware verziót** kell QGC-ben leolvasni; újabb (main / v1.18+) firmware-en egyes enumok bővültek (például `COM_LOW_BAT_ACT`, `NAV_RCL_ACT`). **ELLENŐRIZENDŐ** a konkrét build.
- „Kill” és „Disarm” **nem ugyanaz**. A QGC joystick listában nincs külön „Kill Switch” akció; a vészleállítás neve **Emergency Stop**. Részletek: 3.7. szakasz.

---

## 1. A Holybro X500 V2 Dev Kit hivatalos tartalma

### 1.1 Fontos: a nyilvános források nem teljesen egységesek

Két, egyaránt „hivatalos” leírás létezik, és **eltérnek** a GPS típusában és a flight controller opciókban. A különbség kit-generáció / bundle / elavult dokumentáció következménye lehet. A konkrét példányt **a doboz és a fizikai modulok alapján** kell azonosítani.

| Forrás | Dátumjelzés / állapot | Flight controller | GPS | Power module |
| --- | --- | --- | --- | --- |
| Holybro webshop, *PX4 Development Kit – X500 v2* | aktuális termékoldal (lekérve: 2026-11-14) | **Pixhawk 6C** (Plastic Case) **vagy** **Pixhawk 6X** (v2A baseboard) — vásárláskor választott opció | **M10 GPS** | 6C: **PM02 V3**; 6X: **PM02D** (a 6X most **PM02D-HV**-vel is szállhat) |
| Holybro Docs, *PX4 Development Kit – X500v2* | a Docs oldal „Last updated 1 year ago” jellegű, és csak 6C-t említ | csak **Pixhawk 6C** | **M8N GPS** | **PM02 V3-12S** |
| PX4 Guide, *Holybro X500 V2 + Pixhawk 6C (PX4 Dev Kit)* | aktuális PX4 dokumentáció | a build guide **Pixhawk 6C** + **PM02** | a guide nem emeli ki a chipsetet a címsorban; a kapcsolás: GPS → `GPS1` | **PM02** |
| PX4 Guide, *PX4 Developer Kits* (v1.18) | aktuális kit-összefoglaló | **Pixhawk 6C vagy 6X** | **M10 GPS** | power module (általános említés) |

**Források:**  
[Holybro Store — PX4 Development Kit X500 v2](https://holybro.com/products/px4-development-kit-x500-v2) ·  
[Holybro Docs — X500v2](https://docs.holybro.com/drone-development-kit/px4-development-kit-x500v2) ·  
[PX4 Guide — X500 V2 + Pixhawk 6C](https://docs.px4.io/main/en/frames_multicopter/holybro_x500v2_pixhawk6c) ·  
[PX4 Guide — Developer Kits (v1.18)](https://docs.px4.io/v1.18/en/dev_kits/index)

**ELLENŐRIZENDŐ a konkrét példányon:**

1. Flight controller: **Pixhawk 6C** vagy **Pixhawk 6X** (ház, felirat, baseboard).
2. GPS modul: **M8N**, **M9N** vagy **M10**; továbbá **V1 vagy V2** ház (lásd 1.3 — a V2-n nincs buzzer / safety switch).
3. Telemetria-rádió sáv: **433 MHz** vagy **915 MHz** (az EU-ban a 433 MHz a megfelelő modell; lásd 1.5).
4. Power module: **PM02 V3**, **PM02D** vagy **PM02D-HV**.

Az alábbi lista a **jelenlegi Holybro Store termékoldalt** veszi elsődleges, aktuális forrásnak, és jelzi, ahol a Docs ettől eltér.

### 1.2 A Development Kitben (hivatalosan) benne van

A Store szerint a *PX4 Development Kit – X500 v2* a következőt tartalmazza:

| Tétel | Hivatalos megnevezés | Megjegyzés |
| --- | --- | --- |
| Váz | **X500 V2 Frame Kit (SKU30120)** | Szénszálas alsó/felső lap 144×144 mm, 2 mm; 16 mm-es karcsövek; futómű (16 mm + 10 mm cső); GPS / companion computer furatok; kettős 10 mm × 250 mm sín; akkumulátortartó két hevederrel; szerelőszerszám. Tengelytáv 500 mm. Váz tömeg a Store szerint **610 g**. |
| Motorok | **Holybro 2216 KV920**, 4 db, gyárilag a karokra szerelve | Store: **MR30** csatlakozó. Holybro Docs: **XT30** csatlakozó. **ELLENŐRIZENDŐ** a tényleges csatlakozó a példányon. Részletes villamos adatlapot a Store „Motor Specification can be found here” hivatkozásával említi; a nyilvános Holybro adatlapot e draft készítésekor nem sikerült egyértelműen, stabil URL-lel visszaolvasni — a kW/A/W értékeket ezért **nem** közlöm hivatalos adatként. **ELLENŐRIZENDŐ.** |
| ESC-k | **BLHeli S ESC 20A**, 4 db, **XT30**, **4S akkumulátorral kompatibilis** | Ez a kit **nem** 6S erőforrás. A 20 A-es ESC a motor/prop/4S kombinációt határolja. |
| Propeller | **1045**, 6 db, retainerrel | 4 db üzem + **2 tartalék** a kitben. A retainer a Store szerint a leesés ellen van. |
| Power distribution board | PDB, **XT60** (akkumulátor) + **XT30** (ESC / periféria) | A PX4 build guide szerint a Pixhawk-ot a PM02 táplálja, a motorokat a PDB. |
| Flight controller | **Pixhawk 6C (Plastic Case)** *vagy* **Pixhawk 6X (v2A baseboard)** | A 6C PX4 firmware-rel érkezik (Store / Docs). A PX4 Wiring Quick Start szerint a 6C-hez **SD-kártya jár**. **ELLENŐRIZENDŐ**, hogy a példányban benne van-e, és be van-e helyezve. |
| Power module | 6C: **PM02 V3**; 6X: **PM02D** (esetenként **PM02D-HV**) | A PM02 V3 analog PM, hivatalosan 2–12S / max. 12S, 60 A névleges, 120 A burst, 5,2 V / 3 A regulátor ([PX4 — Holybro PM02 V3](https://docs.px4.io/main/en/power_module/holybro_pm02), [Holybro Store — PM02 V3](https://holybro.com/products/pm02-v3-12s-power-module)). **Figyelem:** a PM 12S-t is elvisel, de a kit ESC-je **csak 4S**. HV verzióhoz PX4-ben `SENS_EN_INA228` engedélyezése kell (Store megjegyzés). |
| GNSS | Store / PX4 Developer Kits: **M10 GPS**. Docs (régebbi): **M8N GPS**. | Compass: a Holybro standard M8N/M9N/M10 modul **IST8310** (egyes M10 darabokon IST8308 is előfordul). M10 firmware-igény: **PX4 1.14+** (Store / Holybro GPS docs). Csatlakozás a PX4 guide szerint: **GPS1**. |
| Telemetria | **SiK Telemetry Radio V3**, **433/915 MHz** (a vásárláskor választott sáv) | Légvégen tipikusan **TELEM1** (PX4: ezen a porton extra konfiguráció nélkül). A földvég USB-n a GCS géphez. |

**Nincs a kitben (Store / Docs egybehangzóan):**

- Akkumulátor. Ajánlás a **jelenlegi Store** szerint: **4S 3000–5000 mAh, 20C+, XT60 LiPo**. A Docs ennél tágabban **4S 2000–5000 mAh**-t említ. A kezelőnek **már van akkumulátora** — cellaszám, kapacitás, C-ráta, csatlakozó és állapot **ELLENŐRIZENDŐ** (csak 4S, XT60, megfelelő állapot).
- RC adó / vevő.
- Depth camera mount.
- Töltő, LiPo-zsák, cellateszter, prop guard (lásd 2. szakasz).

**Külön, opcionális vásárlás (Store / Docs, „Sold Separately”):**

- RadioMaster TX16S RC Controller
- RadioMaster R81 Receiver
- Depth Camera Mount

Ezek az RC-mentes üzemhez **nem kötelezőek**. A kezelő nem használ RC adót, és nem is tervezi.

**Egyéb, a vázra előkészített, de nem a kit része:** Raspberry Pi / Jetson Nano (és hasonló) companion computer hely, Intel RealSense / Structure Core depth camera (külön mount).

**Források:** a fenti Store, Docs és PX4 oldalak; [Holybro M10/M9N/M8N GPS Overview](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/overview); [PX4 Pixhawk 6C Wiring Quick Start](https://docs.px4.io/main/en/assembly/quick_start_pixhawk6c).

### 1.3 GPS: M8N / M9N / M10, és a V1 / V2 különbség (buzzer, safety switch)

A Holybro standard GPS-család (M8N, M9N, M10) V1 változata **beépített iránytűt (IST8310), tricolor LED-et, buzzert és safety switch-et** tartalmaz. ([Holybro GPS Overview](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/overview); [PX4 6C Wiring Quick Start](https://docs.px4.io/main/en/assembly/quick_start_pixhawk6c) — utóbbi M8N/M9N-re mondja ki a beépített buzzer + safety switch + LED-et.)

A Holybro **M9N & M10 V2** change log szerint a V2-ből **kivették a buzzert és a safety switch-et** (valamint a safety LED-et és a GPS Fix LED-et), az EMI/RF zavar csökkentése miatt. ([Holybro GPS Change Logs](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/change-logs))

**ELLENŐRIZENDŐ** a konkrét GPS-házon: van-e fizikai gomb (safety switch) és buzzer. Ha **nincs** (V2):

- A PX4 v1.17.0-ban a `CBRK_IO_SAFETY` alapértéke **22027**, ami a circuit breaker dokumentációja szerint **kikapcsolja az IO safety-t**. Ez a gyári default, tehát safety switch nélkül is armolható a rendszer, *ha* a paraméter a defaulton van. ([PX4 v1.17.0 `circuit_breaker_params.c`](https://github.com/PX4/PX4-Autopilot/blob/v1.17.0/src/lib/circuit_breaker/circuit_breaker_params.c))
- Ha valaki `CBRK_IO_SAFETY = 0`-ra állítja, és nincs kapcsoló, az armolás **blokkolódhat**. **ELLENŐRIZENDŐ** a tényleges érték a FC-n.
- Külön buzzer / safety switch modul **szükséges lehet** (hallható riasztás, fizikai safety). Ez nem a kit automatikus része V2 GPS esetén. **ELLENŐRIZENDŐ**, hogy a kezelő akar-e külön hardvert.
- A PX4 Guide (main) újabb `COM_SAFETY_MODE` paramétert is leír (default: safety always off). Ez a v1.17.0 `commander_params.c`-ben **nem** szerepel; csak akkor releváns, ha az éles firmware újabb. **ELLENŐRIZENDŐ.**

A PX4 6C Wiring Quick Start szerint a beépített safety switch **alapból be van kapcsolva**: 1 másodperces nyomással lehet safety-t oldani, újabb nyomással újra élesíteni (és disarmolni). Ez csak akkor érvényes, ha a példányon van kapcsoló.

### 1.4 Akkumulátor, repülési idő, teher (tájékoztató, nem mérés)

- Store: ~**18 perc** hover, plusz teher nélkül, **5000 mAh** akkumulátorral tesztelve; payload **1500 g** (akkumulátor nélkül, 70% throttle).
- Docs (régebbi): max payload **~1 kg** (4S 5000 mAh, 70% throttle).

A két szám **nem egyezik**. A kész gép **tényleges MTOM / AUW** értékét mérni kell (váz 610 g + motor/ESC/FC/GPS/rádió + akkumulátor + propeller). Ez a biztosítás sávját és a jogi kategóriát is érinti. **ELLENŐRIZENDŐ.**

### 1.5 SiK Telemetry Radio V3

Hivatalos Holybro leírás ([SiK Telemetry Radio V3](https://docs.holybro.com/radio/sik-telemetry-radio-v3)):

- 915 MHz **vagy** 433 MHz modell; „purchase the model that is appropriate for your country/region”.
- Frekvenciasáv a specifikációban: **902–928 MHz** vagy **414–460 MHz**.
- Teljesítményváltozatok: **100 mW** (20 dBm), **500 mW**, **1 W**.
- „Out of the box” hatótáv: tipikusan **jobb, mint 300 m**; 100 mW + 2 dBi antennával a táblázat **300–500 m** LOS-t említ.
- FHSS, MAVLink framing, default soros sebesség 57,6 kbps, default RF data rate 64 kbps.

**ELLENŐRIZENDŐ** a konkrét air/ground páron: 433 vs 915, és 100 mW vs nagyobb teljesítmény. Az EU-ban a 915 MHz-es ISM **nem** a helyi SRD-sáv; Magyarországon / EU-ban a **433 MHz-es** modell a megfelelő választás a gyártó „country/region” útmutatója szerint. A megengedett EIRP / duty cycle a rádiós hatósági szabályok alá esik — a 500 mW / 1 W változat **nem** tekinthető automatikusan engedélymentesnek. **ELLENŐRIZENDŐ** a példány teljesítménye és a hatósági megfelelés, mielőtt nyilvános helyen használnák.

A PX4 Manual Control dokumentációja szerint a joystick parancsok **a telemetria-csatornán**, MAVLinken mennek. Megbízható, elég gyors link kell. ([PX4 — Manual Control](https://docs.px4.io/main/en/config/manual_control); [QGC — Joystick Setup](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/setup_view/joystick.html))

---

## 2. Ami a biztonságos, RC-mentes repüléshez még hiányzik vagy ellenőrizendő

A kezelőnek **van akkumulátora**, **nincs szüksége RC adóra**. Az alábbi lista a kit hivatalos tartalmához és a nyilvános biztonsági / jogi forrásokhoz képest készült.

### 2.1 Hardver / földi felszerelés

| Tétel | Státusz | Megjegyzés |
| --- | --- | --- |
| 4S LiPo, XT60 | A kezelőnek **már van**. | Cellaszám (csak 4S — az ESC 4S), kapacitás, C-ráta, csatlakozó, duzzadás / sérülés, belső ellenállás: **ELLENŐRIZENDŐ**. `BAT1_CAPACITY` beállítása QGC-ben a kapacitás ismerete nélkül a remaining-time / distance-aware logika nem működik (újabb firmware; v1.17-ben is a kapacitás defaultja −1). |
| LiPo töltő (4S, balance) | **A kitben nincs.** Szükséges a biztonságos üzemhez. | Típus / cellavédelem: a konkrét töltő **ELLENŐRIZENDŐ**. |
| LiPo zsák / tűzálló tárolás | **A kitben nincs.** Erősen ajánlott. | Nincs gyártói előírás a kitben; általános LiPo-gyakorlat. |
| Cellateszter / voltmérő | **A kitben nincs.** Ajánlott. | Felszállás előtt cellafeszültség. |
| Tartalék propeller (1045) | A kit **6 db**-ot ad (4+2). | További tartalék ajánlott; nem kötelező a box-listából. |
| Prop guard | **A kitben nincs**, a gyártó nem sorolja a Dev Kithez. | Opcionális, első teszteknél hasznos lehet; 10″ (1045) védőgyűrű kompatibilitása **ELLENŐRIZENDŐ**. Nem helyettesíti a távolságtartást. |
| Buzzer / safety switch | GPS V1: benne van a modulban. GPS V2: **nincs**. | Lásd 1.3. **ELLENŐRIZENDŐ.** |
| microSD a Pixhawk-ban | 6C-hez a PX4 Wiring Guide szerint jár. | Logoláshoz ajánlott; `COM_ARM_SDCARD` default v1.17-ben **1 (Warning only)**. **ELLENŐRIZENDŐ**, hogy be van-e helyezve. |
| Laptop + QGroundControl + PS5 joystick | A kezelő ezzel repül. | A QGC hivatalos „Supported Joysticks” listája **PS3 / PS4**-et említ, **PS5 DualSense-t nem**. A kezelő tapasztalata szerint működik. Ajánlott a **vezetékes USB** (a QGC a PS4-nél a vezetékes USB-t „usually works out of the box”-ként írja). Bluetooth / ds4drv csak a PS4 Linuxos vezeték nélküli leírásban szerepel. PS5 + adott QGC verzió + OS: **ELLENŐRIZENDŐ**. |
| Telemetria-rádió földvég + antenna | A SiK V3 pár része a kitnek. | 433 MHz, rögzített antenna, USB-táp. Hatótáv lásd 1.5 — az első repüléseket jóval 300 m alatt tartani. |
| Tartalék 1045 retainer, szerszám, pót XT60 / sáv | Részben a kitben (szerszám, retainer). | Pótcsatlakozó: **ELLENŐRIZENDŐ**. |
| Elsősegély, tűzoltó (LiPo-ra alkalmas), szélmérő | Nincs a kitben. | Általános földi biztonság, nem gyártói lista. |

### 2.2 Jog: Magyarország / EU (magas szint, hivatalos forrásokkal)

Ez **nem** jogi tanácsadás. A szabályok változnak; a repülés előtt a hatóság és a MyDroneSpace aktuális állapota a mérvadó.

**Nyilvántartás (magyar sajátosság + EU operator ID):**

- Magyarországon a pilóta nélküli légijármű — a játék kivételével — akkor veheti igénybe a magyar légteret, ha a **légiközlekedési hatóság nyilvántartásba vette**. A hatóság nyilvántartást vezet az **UAS üzembentartókról** és az **UAS eszközökről** is. ([Közlekedési Hatóság — UAS nyilvántartás](https://kozlekedesihatosag.kormany.hu/hu/web/legugyi-felugyeleti-hatosagi-foosztaly/pil%C3%B3ta-n%C3%A9lk%C3%BCli-l%C3%A9gij%C3%A1rm%C5%B1-rendszerek-uas-nyilv%C3%A1ntart%C3%A1s%C3%A1val-kapcsolatos-%C3%BCgyint%C3%A9z%C3%A9s); [MyDroneSpace — nyilvántartás](https://mydronespace.hu/tudastar/dron): „Súlyhatártól függetlenül minden pilóta nélküli légijárművedet nyilvántartásba kell venni.”)
- Az eszközön jól láthatóan fel kell tüntetni az **üzembentartói azonosítót (Operator ID)** és az **UAS nyilvántartási számát**.
- EU szinten az üzembentartó a lakóhely szerinti tagállamban regisztrál (2019/947 14. cikk). ([EASA — provisions FAQ](https://www.easa.europa.eu/en/the-agency/faqs/provisions-applicable-both-open-and-specific-category); [EASA — Operator Registration](https://www.easa.europa.eu/en/node/143656))

**Távpilóta-képesítés (nyílt kategória A1/A3):**

- Az X500 V2 Dev Kit MTOM-ja a váz 610 g-ja és a 4S akkumulátor alapján **jóval 250 g felett** van, és **nem C0/C1 osztályjelölésű** fogyasztói drón. Az A1 alkategória (emberek felett, kis gép) erre **nem** alkalmazható.
- A tipikus útvonal a **nyílt kategória A3**, *ha* a gép egyáltalán beléphet a nyílt kategóriába (lásd a következő pontot). A3: érintett személyek **fölé nem**, tőlük és lakott területtől **legalább 150 m** vízszintes távolság, magasság **120 m AGL** alatt. Távpilóta-kompetencia: online képzés + A1/A3 vizsga. ([EASA — Open Category](https://www.easa.europa.eu/en/domains/drones-air-mobility/operating-drone/open-category-low-risk-civil-drones))
- Magyarországon az A1/A3 elméleti tanfolyam a **KTI** felületén, a vizsga a **KAV Vizsgaközpontnál**: 16 év, 40 kérdés / 40 perc, legalább 30 helyes. ([KAV — Drón vizsgáztatás](https://www.vizsgakozpont.hu/dron-vizsgaztatas); [6/2021. (II. 5.) ITM rendelet](https://njt.jog.gov.hu/jogszabaly/2021-6-20-7Q))
- A 6/2021 ITM szerinti **pilóta nélküli légijármű irányítói igazolvány** külön, A2-re épülő nemzeti képesítés. Hogy az adott (hobbi / fejlesztői, nyílt A3) művelethez kell-e, **ELLENŐRIZENDŐ** a hatóságnál; az EU nyílt A3-hoz az A1/A3 igazolás a kért kompetencia.

**Nyílt kategóriába tartozhat-e egy Dev Kit? — kritikus ELLENŐRIZENDŐ:**

Az EASA szerint a nyílt kategóriában csak olyan gép üzemelhet, amely:

- C0–C4 osztályazonosító címkével rendelkezik, **vagy**
- **saját építésű** („privately built”, saját használatra épített, **nem** megvásárolt), **vagy**
- osztályjelölés nélkül, de **csak akkor, ha 2023. december 31. előtt hozták forgalomba**.

Ugyanezen az oldalon az EASA kifejezetten írja: a „privately built” **nem** vonatkozik olyan UAS-re, amelyet **egyetlen, összeszerelésre kész készletként** hoztak forgalomba. A Holybro X500 V2 Dev Kit **ilyen készlet**. ([EASA — Open Category](https://www.easa.europa.eu/en/domains/drones-air-mobility/operating-drone/open-category-low-risk-civil-drones))

**ELLENŐRIZENDŐ a konkrét példányra** (forgalomba hozatal időpontja, van-e C-osztálycímke — várhatóan nincs): ha a darab 2024-től került forgalomba osztályjelölés nélkül, a **nyílt kategória nem feltétlenül alkalmazható**, és **speciális kategória** (engedély / akkreditált nyilatkozat) jöhet szóba. Ezt a magyar légiközlekedési hatóságnál kell tisztázni **az első valós repülés előtt**. Ez a draft nem dönti el a kérdést.

**Direct Remote ID:**

- Az EASA Open Category oldala (2026-11-14-i olvasat) szerint a nyílt kategóriában üzemelő drónoknak — **korlátozott kivételektől eltekintve** — Remote ID rendszerrel kell rendelkezniük, és felsorol olyan, EU megfelelőségi nyilatkozattal ellátott modulokat, amelyek utólag is felszerelhetők. ([EASA — Open Category, Remote identification](https://www.easa.europa.eu/en/domains/drones-air-mobility/operating-drone/open-category-low-risk-civil-drones))
- A klasszikus felosztás szerint a Remote ID főként a **C1, C2, C3, C5, C6** osztályjelölésű gépekre vonatkozik; a C0 / C4 és bizonyos saját építésű esetek kivételek lehetnek. A Dev Kit **nem** C-osztályú, és **nem** „privately built” a fenti definíció szerint.
- A Holybro X500 V2 kit hivatalos tartalmában **nincs** Direct Remote ID modul.

**ELLENŐRIZENDŐ** a hatóságnál: kell-e Direct Remote ID ehhez a példányhoz a tervezett kategóriában. Ha igen, a modul a hiányzó tételek közé tartozik, és a `COM_ARM_ODID` (v1.17: 0 Disabled / 1 Warning / 2 Enforce) később releváns lehet. Jelen default: **0 (Disabled)**.

**Felelősségbiztosítás:**

- Magyarországon **250 g MTOM felett**, vagy kereskedelmi tevékenységnél **kötelező** a harmadik félnek okozott kárra szóló felelősségbiztosítás; a biztosítást a nyilvántartásba vételkor is be kell tudni mutatni. ([Közlekedési Hatóság — UAS nyilvántartás, „Biztosítás”](https://kozlekedesihatosag.kormany.hu/hu/web/legugyi-felugyeleti-hatosagi-foosztaly/pil%C3%B3ta-n%C3%A9lk%C3%BCli-l%C3%A9gij%C3%A1rm%C5%B1-rendszerek-uas-nyilv%C3%A1ntart%C3%A1s%C3%A1val-kapcsolatos-%C3%BCgyint%C3%A9z%C3%A9s); [MyDroneSpace](https://mydronespace.hu/tudastar/dron); 39/2001. (III. 5.) Korm. rendelet, illetve 20 kg / kereskedelmi esetben 785/2004/EK)
- Az X500 V2 **250 g felett van**, tehát a biztosítás **kell**. A fedezeti sáv a mért MTOM-tól függ (a hatóság táblázata szerint hobbi üzemben 0,25–4 kg: 3 000 000 Ft / káresemény). **ELLENŐRIZENDŐ** a mért tömeg és a kötvény.

**Hol szabad repülni:**

- Repülés előtt **MyDroneSpace** (HungaroControl): [https://mydronespace.hu](https://mydronespace.hu) és [http://app.mydronespace.hu](http://app.mydronespace.hu). CTR/MCTR, tiltott / korlátozott / TRA, TMA stb. zónák. ([MyDroneSpace tudástár](https://mydronespace.hu/tudastar/dron))
- VLOS, 120 m AGL (nyílt kategória), érintettek és lakott terület távolsága az A3 szerint, ha az a kategória egyáltalán alkalmazható.

---

## 3. PX4 konfigurációs checklist — RC-mentes (QGC + PS5 + SiK) üzem

**Előfeltétel:** a paramétereket csak **propeller nélkül**, padon, QGC Parameter Editor / Safety oldalon, a **tényleges firmware** dokumentációjával összevetve szabadna majd beállítani. Ez a szakasz **ajánlott értéket és indoklást** ad, nem automatikus átírást.

QGC: Vehicle Setup → Safety a failsafe-ek nagy részéhez; a többi a Parameter Editorban. ([PX4 — Safety Configuration](https://docs.px4.io/v1.17/en/config/safety), [main](https://docs.px4.io/main/en/config/safety))

Alap airframe a PX4 guide szerint: **Quadrotor x → Holybro 500 V2**, plusz szenzor-kalibráció, actuator mapping, Safety oldal. ([PX4 — X500 V2 build](https://docs.px4.io/main/en/frames_multicopter/holybro_x500v2_pixhawk6c))

### 3.1 `COM_RC_IN_MODE` — hogy a hiányzó RC ne blokkoljon és ne indítson téves RC-loss failsafet

| | |
| --- | --- |
| Paraméter | `COM_RC_IN_MODE` |
| PX4 v1.17.0 default | **3** — RC or MAVLink keep first (az első érvényes forráshoz lock, rebootig) |
| RC-mentes ajánlás | **1 — MAVLink only** |

Értékek (v1.17.0 `commander_params.c` és [PX4 Manual Control](https://docs.px4.io/main/en/config/manual_control)):

| Érték | Jelentés |
| --- | --- |
| 0 | RC only (RC kalibráció kell) |
| **1** | **MAVLink only — az RC és a kapcsolódó RC-ellenőrzések ki vannak kapcsolva** |
| 2 | RC vagy MAVLink, fallback, ha a aktuális forrás érvénytelen lesz |
| 3 | Első érvényes forrás lock (default) |
| 4 | Minden manuális forrás tiltva |
| 5–8 | Prioritásos RC / MAVLink instance sorrendek |

A QGC Joystick Setup oldala külön is előírja: joystick támogatáshoz PX4-en `COM_RC_IN_MODE = 1` (ott „Joystick”-ként említik; a PX4 paraméternév szerinti címke: **MAVLink only**). ([QGC — Joystick Setup](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/setup_view/joystick.html))

**Miért 1, ne 3:** default 3-nál, ha nincs kalibrált RC, az RC-ellenőrzések tovább futnak minden olyan opciónál, amely RC-t is enged (a Manual Control docs: „RC checks are run for any option that uses RC, so not for MAVLink only”). Ez armolási hibát vagy váratlan manual-control-loss viselkedést okozhat. A **1** explicit módon csak a MAVLink `MANUAL_CONTROL` (QGC joystick) forrást engedélyezi, és **kikapcsolja az RC checkeket**.

### 3.2 Manual control loss: `NAV_RCL_ACT`, `COM_RC_LOSS_T`, `COM_FAIL_ACT_T`

Akkor lép életbe, ha a `COM_RC_IN_MODE` által engedett manuális források közül **egyik sem** él a timeouton túl. ([PX4 Safety — Manual Control Loss](https://docs.px4.io/v1.17/en/config/safety))

| Paraméter | v1.17.0 default | Jelentés | RC-mentes megjegyzés |
| --- | --- | --- | --- |
| `COM_RC_LOSS_T` | **0,5 s** | Ennyi ideig nincs új setpoint RC-től vagy joysticktól, utána elveszettnek számít a manuális irányítás. Röviden kell tartani: a gép a **utolsó botállást** tartja a timeoutig. | A QGC idle-ban is küld joystickot (QGC docs: idle 5 Hz, mozgásnál magasabb, default 25 Hz). 0,5 s általában megfelelő; ne legyen rövidebb, mint az update. |
| `COM_FAIL_ACT_T` | **5 s** | A detektálás után ennyit Hold-ban vár, mielőtt a failsafe akció lefut. 0 = azonnal. | Szándékos késleltetés a intermittens drop ellen. RC-mentes üzemben a linkvesztés után nincs bot-override; a GCS módváltás is csak akkor él, ha a datalink még megvan (joystick kihúzás, rádió benn). |
| `NAV_RCL_ACT` | **2** (Return mode) | 1 Hold, 2 Return, 3 Land, 5 Terminate, 6 Disarm. (A main docs later **7 = Hold, no failsafe** értéket is leír; ez a v1.17.0-ban **nincs**.) | Default Return ésszerű, **ha van érvényes home + globális pozíció**. Szűk térben Land (3) indokolható. **Disarm / Terminate a levegőben zuhanást jelent — ne.** |
| `COM_RCL_EXCEPT` | **0** | Bitmező: mely módokban legyen figyelmen kívül hagyva a manual control loss (Mission, Hold, Offboard, …). | Mission közben is általában **kell** a failsafe (0). Csak tudatos kivétel.

**Mikor lép ez életbe RC-mentes üzemben:**

- A **PS5 / joystick leválik** a laptopról, de a SiK / QGC datalink **megmarad** → jellemzően **csak** a manual control loss (`NAV_RCL_ACT`).
- A **SiK rádió** szakad meg → joystick **és** GCS datalink egyszerre vész el (lásd 3.3).

### 3.3 Datalink loss: `NAV_DLL_ACT` + `COM_DL_LOSS_T` — a három failsafe-téma közül ez a legfontosabb lyuk

| Paraméter | v1.17.0 default | Jelentés |
| --- | --- | --- |
| `COM_DL_LOSS_T` | **10 s** (min. 5, max. 300) | Ennyi ideig nincs GCS datalink, utána a failsafe. |
| `NAV_DLL_ACT` | **0 — Disabled** | 0 Disabled, 1 Hold, 2 Return, 3 Land, 5 Terminate, 6 Disarm |
| `COM_DLL_EXCEPT` | **0** | Módkivételek (Mission / Hold / Offboard) |

Forrás: [PX4 Safety — Data Link Loss](https://docs.px4.io/v1.17/en/config/safety); PX4 v1.17.0 `commander_params.c`.

**A gyári default (`NAV_DLL_ACT = 0`) RC-mentes üzemben nem elfogadható.** Itt a SiK rádió **egyszerre** a joystick és a QGC linkje. Ha a rádió elmegy, és a datalink-failsafe Disabled, a gép csak a (rövidebb) manual-control-loss úton védett — de a két timeout és a két akció **eltérhet**, és egy későbbi firmware / módkivétel mellett ez rés lehet. A Safety docs szerint, ha több failsafe egyszerre él, a **súlyosabb** akció fut le.

**Ajánlás (indoklással):**

- `NAV_DLL_ACT = 2` (**Return**), **vagy** `3` (**Land**), soha ne hagyjuk Disabled-en.
- **Return** akkor ajánlott, ha van megbízható GPS / home, a RTL útvonal tiszta, és a gép távolabb is lehet a pilótától: a gép visszamegy, ahelyett hogy ott landolna, ahol a link elveszett (fa, út, szomszéd kertje).
- **Land** akkor indokolható, ha az első repülések szűk, előre kijelölt, akadálymentes mezőn, alacsony magasságon, a pilóta közelében történnek, és a Return emelkedése / 60 m-es default RTL-magasság nem kívánatos.
- `COM_DL_LOSS_T`: a 10 s default ésszerű a SiK intermittens dropjai ellen; 5 s a minimum a paraméterben. Nagyon rövidre venni hamis RTL-t okozhat.
- Terminate / Disarm datalink-vesztésre: **nem** ajánlott (zuhanás).

A `COM_RC_LOSS_T` (0,5 s) + `COM_FAIL_ACT_T` (5 s) **hamarabb** leütheti a Return/Land-et, mint a 10 s-os datalink timeout, ha mindkét failsafe ugyanarra az akcióra van állítva. Ez önmagában nem baj, de tudni kell: rádió kihúzásakor várhatóan a **manual control loss** fut le előbb.

### 3.4 RTL paraméterek

Multicopter default return típus: `RTL_TYPE = 0` (home / rally, közvetlen, geofence-tudatos vízszintes út). ([PX4 — Return Mode, Multicopter](https://docs.px4.io/main/en/flight_modes_mc/return); v1.17.0 `rtl_params.c`)

| Paraméter | v1.17.0 default | Megjegyzés |
| --- | --- | --- |
| `RTL_RETURN_ALT` | **60 m** | Minimum visszatérési magasság a cél felett; ha már magasabban van, azt tartja. Első, sík, akadálymentes mezőn **csökkenthető** (pl. a helyi akadályok + tartalék), de a 120 m AGL jogi plafont ne lépje túl. **ELLENŐRIZENDŐ** a terep. |
| `RTL_DESCEND_ALT` | **30 m** | Ezen a magasságon vár / loiter, ha `RTL_LAND_DELAY ≠ 0`. |
| `RTL_LAND_DELAY` | **0 s** | 0: érkezés után landol. −1: loiter, nincs automatikus landolás. |
| `RTL_MIN_DIST` | **10 m** | Ezen a távolságon belül a kúp (`RTL_CONE_ANG`) számítja a minimum magasságot. |
| `RTL_CONE_ANG` | **45°** | 0 = mindig `RTL_RETURN_ALT`. |
| `RTL_TYPE` | **0** | Home / legközelebbi safe point. |

RTL-hez kell **globális pozíció és beállított home**. GPS nélkül ez a failsafe nem hajtható végre megbízhatóan — ezért az első valós repülésen `COM_ARM_WO_GPS` ne engedjen „csak warning” mellett GPS nélküli armolást (lásd 3.8).

### 3.5 Geofence: `GF_ACTION`, `GF_MAX_HOR_DIST`, `GF_MAX_VER_DIST`

| Paraméter | v1.17.0 default | Jelentés |
| --- | --- | --- |
| `GF_ACTION` | **2** (Hold mode) | 0 None, 1 Warning, 2 Hold, 3 Return, 4 Terminate, 5 Land |
| `GF_MAX_HOR_DIST` | **0** (kikapcsolva) | Home-középpontú henger vízszintes sugara, méter. 0 = off. |
| `GF_MAX_VER_DIST` | **0** (kikapcsolva) | Magasság a home felett, méter. 0 = off. |
| `GF_SOURCE` | **0** (GPOS) | 0 becsült globális pozíció, 1 nyers GPS |

Forrás: [PX4 Safety — Geofence](https://docs.px4.io/v1.17/en/config/safety); v1.17.0 `geofence_params.c`.

A failsafe **csak a sértés után** lép. Szigorú no-fly / jogi határnál a docs **Hold**-ot és **biztonsági margót** javasol (a jármű féktávolsága + reakcióidő). `GF_ACTION = 4` (Terminate) zuhanást / motorkillt jelent, és a `CBRK_FLIGHTTERM` (v1.17 default **121212** = termination action Disabled a FailureDetector úton; a geofence Terminate viselkedését a Safety docs szerint ez a breaker befolyásolja) miatt sem ajánlott első üzemre.

**Ajánlás első repülésre:** a henger legyen **bekapcsolva**, ne 0-n maradjon. Például — a helyszínhez igazítva, **ELLENŐRIZENDŐ** — vízszintes sugár a tesztmezőn belül (jóval az A3 150 m-es „emberektől / lakott terület” követelménye és a SiK megbízható zónája alatt), függőlegesen pedig a tervezett max. magasság + tartalék, 120 m AGL alatt. Akció: **Hold (2)** vagy **Return (3)**.

### 3.6 Alacsony akku: `BAT_*`, `COM_LOW_BAT_ACT`

| Paraméter | v1.17.0 default | Jelentés |
| --- | --- | --- |
| `BAT_LOW_THR` | **0,15** (15%) | Figyelmeztetési küszöb (arány, nem százalékpont a névben: `norm`) |
| `BAT_CRIT_THR` | **0,07** (7%) | Kritikus; tipikusan RTL, ha a failsafe úgy van állítva |
| `BAT_EMERGEN_THR` | **0,05** (5%) | Vész; tipikusan azonnali Land |
| `BAT1_CAPACITY` | **−1** (nincs megadva) | mAh; be kell írni a tényleges akku kapacitását |
| `BAT_AVRG_CURRENT` | **15 A** | Tipikus repülési áram a remaining-time becsléshez; **ELLENŐRIZENDŐ** méréssel |
| `COM_LOW_BAT_ACT` | **0 — Warning only** | v1.17: **0** Warning, **2** Land, **3** Return a critical szinten, Land az emergency szinten |
| `COM_ARM_BAT_MIN` | **−1** | Negatív: a `BAT_CRIT_THR` az armolási küszöb |

Forrás: [PX4 Safety — Battery](https://docs.px4.io/v1.17/en/config/safety); v1.17.0 battery `module.yaml` + `commander_params.c`.

A **main / újabb** PX4 a `COM_LOW_BAT_ACT` értékeit bővítette (distance-aware return, 3–6). Az éles firmware-en a QGC enumlistát kell nézni, nem vakon a v1.17 táblát.

**Ajánlás:** `COM_LOW_BAT_ACT = 3` (v1.17 jelentés: Return criticalnál, Land emergency-nél). A default **csak figyelmeztet** — RC-mentes, valós repülésen ez kevés. `BAT1_CAPACITY` legyen a mért / címke szerinti mAh. A százalékos küszöbök csak akkor értelmesek, ha a PM02 / feszültség-áram kalibráció (`BAT1_*` meredekség, vágási feszültség) rendben van — **ELLENŐRIZENDŐ** QGC Battery beállítás, 4S cellaszám.

### 3.7 PS5 gomb: QGC „Emergency Stop” vs „Disarm” vs PX4 Kill Switch

A QGC joystick gombjaihoz rendelhető akciók között (QGC `Joystick.h`, master, 2026-11-14) szerepel többek között:

- **Arm**
- **Disarm**
- **Toggle Arm**
- **Emergency Stop**

**Nincs** „Kill”, „Kill Switch” vagy „Emergency kill” nevű külön akció a listában.

**Emergency Stop** (ezt kell az in-flight vészgombhoz rendelni):

- A QGC `Vehicle::emergencyStop()` **`MAV_CMD_COMPONENT_ARM_DISARM`** parancsot küld, `param1 = 0`, `param2 = 21196` („magic number for emergency stop”). Ez **kényszerített disarm**, levegőben is. ([QGC `Vehicle.cc`](https://github.com/mavlink/qgroundcontrol/blob/master/src/Vehicle/Vehicle.cc))
- Fizikai hatás: a motorok **azonnal leállnak**, a gép **esik**. Ez nem RTL és nem Land.

**Disarm** (ne ezt tedd egyetlen vészgombnak):

- Normál disarm (`setArmed(false)`).
- PX4 szerint magasságtartó módokban (pl. Position) **csak landolás detektálása után** lehet gesztussal / gombbal disarmolni. Stabilized / Acro / Manual módban a levegőben is lehet. ([PX4 — Arm, Disarm, Prearm](https://docs.px4.io/main/en/advanced_config/prearm_arm_disarm))
- Position módban egy sima **Disarm gomb a levegőben elutasítható**, amikor éppen vészleállítás kellene.

**PX4 Kill Switch / Kill Gesture** (ne keverjük a QGC gombbal):

- A **Kill Switch** a PX4 Safety docs szerint **RC kapcsoló**: azonnal nullázza a motor kimeneteket; **5 másodpercen belül visszafordítható**. ([PX4 Safety — Emergency Switches](https://docs.px4.io/main/en/config/safety))
- A **Kill Gesture** (`MAN_KILL_GEST_T`, default −1 = Disabled) szintén azonnali motortleállás, de **reboot nélkül nem visszafordítható**.

**Beállítás (QGC):**

1. Vehicle Setup → **Joystick**; aktív eszköz a PS5.
2. **Calibrate** (a joystick csak a kalibráció utolsó lépéseként lesz enabled — QGC docs).
3. Axis / Button monitoron ellenőrizni minden tengelyt és gombot.
4. Egy **nehezen véletlenül megnyomható** gombhoz: **Emergency Stop**.
5. Külön gombokhoz (nem ugyanaz, mint a vészleállítás): pl. **Arm** / **Disarm**, módváltások.
6. Throttle mód: a QGC Advanced fülön a gamepad tipikusan „Full down stick is zero throttle” vagy „Center stick is zero throttle” — **ELLENŐRIZENDŐ**, hogy a PS5 trigger/stick kiosztás 0 throttlét adjon alul, ne középen. Rossz throttle-mód armoláskor felpörgetheti a motorokat.

**Figyelmeztetés:** Emergency Stop ≠ Land, ≠ RTL, ≠ sima Disarm. Levegőben használva a gép **esik**. Csak valódi vészhelyzetre (pl. ellenőrizhetetlen viselkedés, emberek felé sodródás, amikor a Land/RTL már nem biztonságos).

### 3.8 `COM_ARM_*` ellenőrzések

v1.17.0 értékek (`commander_params.c`), plusz [PX4 Safety — Arming](https://docs.px4.io/v1.17/en/config/safety) és [Prearm](https://docs.px4.io/main/en/advanced_config/prearm_arm_disarm):

| Paraméter | v1.17 default | Ajánlás RC-mentes első repülésre |
| --- | --- | --- |
| `COM_ARMABLE` | 1 (engedélyezve) | Maradjon 1, kivéve karbantartás alatt (0). |
| `COM_ARM_WO_GPS` | **1** (Warning only; 0 = Deny, 2 = Disabled) | Első autonóm / RTL-képes repülésre: **0 (Deny arming GPS nélkül)**. A default csak figyelmeztet. |
| `COM_ARM_MIS_REQ` | 0 | 0 rendben manuális / Position első hoverhez. Misszióhoz 1 indokolt lehet. |
| `COM_ARM_SDCARD` | **1** (Warning) | Legalább warning; logoláshoz 2 (enforce) fontolható. |
| `COM_ARM_ODID` | 0 (Disabled) | Csak akkor emelendő, ha Remote ID tényleg kell és fel van szerelve. |
| `COM_ARM_BAT_MIN` | −1 (= `BAT_CRIT_THR`) | Megtartható, vagy szigorítható (pl. 0,30). |
| `COM_DISARM_LAND` | 2 s | Autó-disarm landolás után. |
| `COM_DISARM_PRFLT` | 10 s | Ha armolás után nem emelkedik, disarm. |
| `COM_ARM_IMU_ACC` / `COM_ARM_IMU_GYR` / `COM_ARM_MAG_ANG` / `COM_ARM_MAG_STR` / `COM_CPU_MAX` | gyári | Ne kapcsoljuk ki a pre-arm ellenőrzéseket a „hogy induljon” kedvéért. |

QGC Fly View → Flight Status / Arming Check Report: felszállás előtt **minden** piros ellenőrzés legyen tisztázva.

### 3.9 A három failsafe-téma röviden, együtt

RC-mentes, egyetlen SiK linken:

1. **`COM_RC_IN_MODE = 1`**, hogy a hiányzó RC ne blokkoljon és ne hamis RC-loss-t hozzon.
2. **`NAV_RCL_ACT = 2` (Return)** — joystick elvesztésére (a datalink még élhet).
3. **`NAV_DLL_ACT = 2` (Return)** — a **gyári 0 (Disabled) helyett**; ez a GCS/SiK link elvesztése. Ugyanarra az akcióra érdemes tenni, mint a manual control loss-t, tudva, hogy a rövidebb `COM_RC_LOSS_T` általában előbb tüzel, ha a rádió teljesen elmegy.

Mellé: bekapcsolt geofence, `COM_LOW_BAT_ACT = 3`, GPS nélküli armolás tiltása, Emergency Stop a PS5-ön.

---

## 4. Földi tesztek (propeller LE)

**Kötelező:** minden alábbi teszt **levett propellerrel**. Az ESC-k és motorok kaphatnak feszültséget, a rotorok **nem** foroghatnak veszélyesen — a 1045 prop off. A gép rögzítve, emberek a rotorsíkon kívül, LiPo tűzvédelmi rendben. A ByteWolf fizikai zár **zárva marad**; ezeket a teszteket a kezelő QGC-ből, a saját FC-jén végzi, nem a platformon keresztül.

Jegyzőkönyvbe: dátum (Europe/Budapest), firmware verzió, paraméterértékek QGC-ből kiolvasva (képernyőkép / paraméter-export), várt vs. tényleges viselkedés. Titok / hostname / IP nélkül.

### 4.1 Előkészítés

1. Propellerek leszerelve, retainerek eltéve.
2. `COM_RC_IN_MODE`, `NAV_RCL_ACT`, `NAV_DLL_ACT`, `COM_RC_LOSS_T`, `COM_DL_LOSS_T`, `COM_FAIL_ACT_T`, `COM_LOW_BAT_ACT`, geofence, RTL, `COM_ARM_WO_GPS` kiolvasva és feljegyezve.
3. QGC joystick kalibrálva, **Emergency Stop** gomb kiosztva.
4. Safety switch: ha van, a PX4 docs szerint 1 s nyomással oldani, mielőtt armolni lehet.
5. GPS, ha beltér: a datalink / joystick tesztek egy része beltérben is végezhető, de az RTL / geofence / GPS-lock tesztekhez **kültéri, nyílt, GPS-es** pad kell. Beltéri armolásnál a `COM_ARM_WO_GPS` ideiglenes lazítása **csak tudatosan, propeller nélkül**, majd **visszaállítás** — különben az első repülésre GPS nélkül is elindulhat a gép.

### 4.2 `COM_RC_IN_MODE` armolási teszt (props off)

**Cél:** a hiányzó RC ne akadályozza az armolást, joystickkal viszont igen.

1. RC vevő nincs, vagy nincs bekötve.
2. `COM_RC_IN_MODE = 1`, QGC + PS5 él, kalibrálva.
3. Pre-arm checkek zöldek (amennyire a környezet engedi).
4. Arm QGC-ből vagy a kiosztott **Arm** gombról.
5. **Várt:** arm sikerül (vagy csak dokumentált, más okú pre-arm hiba van, nem „No RC” / RC check).
6. Disarm.
7. (Opcionális ellenpróba, props off): `COM_RC_IN_MODE` ideiglenesen 0 (RC only) — **várt:** armolás elutasítva RC hiányában. Utána **vissza 1-re**.

### 4.3 Kill / Emergency Stop teszt (props off, armolva)

**Cél:** a PS5 Emergency Stop azonnal leállítja a motorok kimenetét / disarmol.

1. Props off, gép rögzítve.
2. Arm. A motorok jelet kaphatnak (kattogás / lassú pörgés props nélkül is előfordulhat) — kezek távol.
3. **Emergency Stop** gomb.
4. **Várt:** azonnali disarm / motorkimenet stop; QGC-n armed → disarmed; a sima „Disarm” elutasítása itt nem releváns, mert a 21196-os kényszerdisarmnak át kell mennie.
5. Újraarmolás: **várt**, hogy armolható (ellentétben a PX4 Kill Gesture-rel, amely rebootot kér). Ha nem, feljegyezni.

Soha ne ezt a tesztet fusson propellerrel.

### 4.4 Joystick disconnect (props off, armolva, datalink megmarad)

**Cél:** csak a manuális irányítás vész el, a QGC link él.

1. Props off, arm, lehetőleg Position / Hold, ha a GPS engedi; különben a mód legyen feljegyezve.
2. PS5 USB kihúzása (SiK és QGC csatlakozva marad).
3. Mérni: `COM_RC_LOSS_T` + `COM_FAIL_ACT_T` után mi történik.
4. **Várt:** QGC failsafe üzenet; mód `NAV_RCL_ACT` szerint (Return / Land / Hold). Return/Land beltérben / GPS nélkül **nem** fog rendesen lefutni — ezt a tesztet GPS-lockkal, kültéren, props off, a gép **a földön rögzítve** érdemes csinálni, és csak a **módváltást / failsafe jelzést** igazolni, nem a tényleges felszállást.
5. Joystick visszakötése: dokumentálni, visszatér-e a manuális irányítás, kell-e módváltás.

### 4.5 Telemetria-rádió (SiK) szétkapcsolás (props off, armolva)

**Cél:** a várt datalink- és/vagy manual-control-loss failsafe tényleg lefut. Ez a valós „elfogyott a link” eset.

1. Props off, gép rögzítve, lehetőleg kültéri GPS-lock (hogy a Return egyáltalán érvényes mód legyen).
2. Arm.
3. A **földi SiK USB** kihúzása, **vagy** a légi rádió tápjának bontása — úgy, hogy semmi ne forogjon, és a kábelbontás biztonságos legyen.
4. Mérni az időt a szakadástól a failsafe viselkedésig.
5. **Várt (ha a 3. szakasz be van állítva):**
   - QGC elveszti a járművet (földi USB kihúzásakor a GCS oldali kép eltűnik — a FC oldali failsafe-et a visszakapcsolás utáni log / a FC buzzer / a mód a reconnect után, illetve SD log igazolja).
   - A FC a `COM_RC_LOSS_T` / `COM_FAIL_ACT_T` és/vagy `COM_DL_LOSS_T` után `NAV_RCL_ACT` / `NAV_DLL_ACT` szerinti módba lép. SD log (`failsafe`, `vehicle_status`, `commander`) a tényszerű bizonyíték, mert QGC éppen akkor nincs a linken.
6. Link vissza, disarm, log mentése.

**Fontos:** a földi USB kihúzásakor a kezelő **nem** látja élőben a QGC-t. A tesztnek része legyen a log-visszaolvasás. Alternatíva: második, független telemetria nincs a kitben — **ELLENŐRIZENDŐ**, hogy csak a logra támaszkodunk-e, vagy USB-tether beltéri padon (USB-tether viszont más failsafe-képet ad, mint a SiK, és a `CBRK_USB_CHK` default v1.17-ben 197848, azaz a USB-check ki van kapcsolva).

### 4.6 Egyéb földi, props off

- Stick irányok: roll / pitch / yaw / throttle a QGC HUD-on a várt irányba mennek; throttle alul = 0.
- Arm / Disarm külön gombról.
- Low-battery failsafe **nem** tesztelhető biztonságosan merülő akkumulátorral a levegőben; props off mellett a küszöbök és a `COM_LOW_BAT_ACT` kiolvasása, plusz (opcionális) a Failsafe State Machine Simulation a PX4 docsban. Éles merítéses teszt **ELLENŐRIZENDŐ**, és csak props off / padon, nem repülésben.
- Geofence: csak akkor, ha a henger be van kapcsolva; props off, a gép mozdulatlansága miatt a sértés nem szimulálható reálisan a padon. A paraméterek kiolvasása + QGC map fence megjelenítés.

**Átment a földi sor:** minden várt failsafe igazolva loggal vagy QGC üzenettel, paraméterek visszaolvasva, props továbbra is le.

---

## 5. Első repülés — rövid eljárás (csak a 4. szakasz PASS után)

Ez **nem** jóváhagyás. A ByteWolf fizikai zár zárva marad. Csak akkor hajtható végre, ha a jogi pontok (2.2) tisztázottak, a biztosítás és a nyilvántartás él, a hatóság / MyDroneSpace engedi a helyszínt, és a kezelő döntése szerint a gép készen áll.

1. **Helyszín:** nyílt, akadálymentes mező; MyDroneSpace zöld / engedélyezett; A3 távolságok (ha a nyílt kategória alkalmazható — különben a hatósági engedély szerinti hely); szél, látás, VLOS; illetéktelenek távol. SiK hatótávon belül, az első eltávolodás legyen kicsi (tízes méterek, nem százak).
2. **Jog / papír:** üzembentartói + eszközazonosító a gépen; A1/A3 igazolvány; kötvény; Remote ID státusz, ha a hatóság szerint kell.
3. **Gép:** vizuális ellenőrzés (váz, csavarok, motorvezetékek, GPS irányjelző előre, antenna, propeller-irány a PX4 guide szerint: a motorirány a lapkán és a propeller színezése). **4S** akku, cellák egyenletesek, nincs duzzadás, XT60 rögzítve, LiPo heveder. SD kártya bent. Propellerek csak közvetlenül a felszállás előtt, a földi teszt után.
4. **QGC:** firmware / airframe / kalibráció aktuális; paraméterek a 3. szakasz szerint **visszaolvasva**; joystick kalibrálva; Emergency Stop gomb bemondva / „dry” megnyomva, armolás nélkül; failsafe lista átnézve.
5. **Link:** SiK csatlakozik, RSSI / minőség elfogadható, joystick élő a Button monitoron.
6. **GPS / home:** fix, home a felszállási ponton, geofence aktív, `COM_ARM_WO_GPS = 0`.
7. **Arm:** props fel, terület tiszta, hangos „arming”, arm, 2–3 mp a földön, motorok stabilak, azonnali Emergency Stop tervben.
8. **Első manőver:** alacsony hover (néhány másodperc, 1–2 m), Position mode ha a GPS engedi; irányok ellenőrzése; land; auto-disarm (`COM_DISARM_LAND`).
9. **Rövid második kört** csak az első tiszta landolás után: kis elmozdulás, majd **kézi RTL** (QGC Return), hogy az útvonal és a magasság a várt legyen — nem failsafe-szimuláció a levegőben rádió kihúzással (azt props off, padon már elvégeztük).
10. **Leszállás után:** disarm, props le, akku le, log mentése, rövid jegyzőkönyv (ami jól ment, ami nem). Nincs „még egy kör” merülő akkuval.

**Szándékosan nincs** a listában: SiK szétkapcsolás a levegőben, Emergency Stop „próbából” hoverben, város, emberek közelében, BVLOS, ByteWolf zár feloldása, éles misszió.

---

## 6. Nyitott ELLENŐRIZENDŐ tételek (összesítve)

1. A konkrét kit: 6C vagy 6X; M8N vagy M10; GPS V1 vagy V2 (buzzer / safety switch); SiK 433 vagy 915, és hány mW; PM02 V3 / PM02D / PM02D-HV.
2. Motorcsatlakozó: MR30 vs XT30 (Store vs Docs).
3. Tényleges AUW / MTOM (biztosítási sáv, jogi kategória).
4. A példány forgalomba hozatalának ideje és C-osztálycímke — **nyílt kategória egyáltalán alkalmazható-e**.
5. Direct Remote ID kötelezettség erre a gépre Magyarországon.
6. Kell-e a 6/2021 ITM irányítói igazolvány a tervezett művelethez, vagy elég az A1/A3.
7. A kezelő 4S akkuja: kapacitás, C-ráta, állapot; töltő és LiPo zsák megléte.
8. Telepített PX4 verzió vs. a v1.17.0 enumok (különösen `COM_LOW_BAT_ACT`, `NAV_RCL_ACT`, `COM_SAFETY_MODE`).
9. PS5 + adott QGC verzió hivatalosan nem szerepel a Supported Joysticks listán.
10. EU/HU rádiós megfelelés a SiK teljesítményére.
11. Prop guard szükségessége / kompatibilitás (opcionális).
12. Holybro 2216 KV920 részletes, gyártói villamos adatlap (stabil URL).

---

## 7. Források

- [Holybro Store — PX4 Development Kit X500 v2](https://holybro.com/products/px4-development-kit-x500-v2)
- [Holybro Docs — PX4 Development Kit X500v2](https://docs.holybro.com/drone-development-kit/px4-development-kit-x500v2)
- [Holybro Store — X500 V2 Kits (ARF, külön a teljes Dev Kittől)](https://holybro.com/products/x500-v2-kits)
- [PX4 Guide — Holybro X500 V2 + Pixhawk 6C](https://docs.px4.io/main/en/frames_multicopter/holybro_x500v2_pixhawk6c)
- [PX4 Guide — Developer Kits (v1.18)](https://docs.px4.io/v1.18/en/dev_kits/index)
- [PX4 Guide — Pixhawk 6C Wiring Quick Start](https://docs.px4.io/main/en/assembly/quick_start_pixhawk6c)
- [Holybro — M8N / M9N / M10 GPS Overview](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/overview)
- [Holybro — GPS Change Logs (V2: nincs buzzer / safety switch)](https://docs.holybro.com/gps-and-rtk-system/m8n-m9n-m10-gps/standard-m10-m9n-m8n-gps/change-logs)
- [Holybro — SiK Telemetry Radio V3](https://docs.holybro.com/radio/sik-telemetry-radio-v3)
- [PX4 Guide — Holybro PM02 V3](https://docs.px4.io/main/en/power_module/holybro_pm02)
- [PX4 Guide — Manual Control](https://docs.px4.io/main/en/config/manual_control)
- [PX4 Guide — Safety (v1.17)](https://docs.px4.io/v1.17/en/config/safety) · [main](https://docs.px4.io/main/en/config/safety)
- [PX4 Guide — Return Mode (Multicopter)](https://docs.px4.io/main/en/flight_modes_mc/return)
- [PX4 Guide — Arm, Disarm, Prearm](https://docs.px4.io/main/en/advanced_config/prearm_arm_disarm)
- PX4 v1.17.0 forrás: `commander_params.c`, `geofence_params.c`, `rtl_params.c`, `circuit_breaker_params.c`, `src/lib/battery/module.yaml` — [tag v1.17.0](https://github.com/PX4/PX4-Autopilot/tree/v1.17.0)
- [QGC Guide — Joystick Setup](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/setup_view/joystick.html)
- QGC forrás: `Joystick.h` (Arm / Disarm / Emergency Stop), `Vehicle.cc` (`emergencyStop`, `MAV_CMD_COMPONENT_ARM_DISARM` + 21196)
- [EASA — Open Category](https://www.easa.europa.eu/en/domains/drones-air-mobility/operating-drone/open-category-low-risk-civil-drones)
- [EASA — Operator registration, Remote ID, insurance](https://www.easa.europa.eu/en/node/143656)
- [EASA — Provisions FAQ](https://www.easa.europa.eu/en/the-agency/faqs/provisions-applicable-both-open-and-specific-category)
- [Közlekedési Hatóság — UAS nyilvántartás](https://kozlekedesihatosag.kormany.hu/hu/web/legugyi-felugyeleti-hatosagi-foosztaly/pil%C3%B3ta-n%C3%A9lk%C3%BCli-l%C3%A9gij%C3%A1rm%C5%B1-rendszerek-uas-nyilv%C3%A1ntart%C3%A1s%C3%A1val-kapcsolatos-%C3%BCgyint%C3%A9z%C3%A9s)
- [MyDroneSpace tudástár](https://mydronespace.hu/tudastar/dron)
- [KAV — Drón vizsgáztatás](https://www.vizsgakozpont.hu/dron-vizsgaztatas)
- [6/2021. (II. 5.) ITM rendelet](https://njt.jog.gov.hu/jogszabaly/2021-6-20-7Q)
- (EU) 2019/947 és 2019/945; 1995. évi XCVII. törvény; 39/2001. (III. 5.) Korm. rendelet

---

*Draft vége. Nem lezárt elfogadási jegyzőkönyv, nem merge-előfeltétel, és nem a fizikai zár feloldása.*
