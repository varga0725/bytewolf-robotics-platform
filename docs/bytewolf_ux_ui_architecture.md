# ByteWolf Robotics Platform — Teljes UX/UI és Termékdesign Architektúra Specifikáció
**Verzió:** v1.0
**Szerző:** Jules, Senior Product Designer & UX/UI Architect
**Státusz:** Jóváhagyott / Fejlesztésre előkészítve
**Alapelv:** *One Brain. Many Bodies.*

---

## 1. Termékkontextus és UX Értelmezés

A **ByteWolf Robotics Platform** egy úttörő, AI-native robotikai operációs rendszer és vezérlőfelület, amely szakít a hagyományos, egyedi robothardverekhez láncolt siló-alkalmazásokkal. A platform alapfilozófiája:

> **"One Brain. Many Bodies." (Egy agy. Számos test.)**

Ez azt jelenti, hogy a kognitív funkciókért, természetes nyelvű tervezésért, szemantikus memóriáért és környezeti világmodell-építésért felelős intelligens réteg (**ByteWolf Cognitive Runtime**) teljesen független az éppen vezérelt fizikai formától (legyen az repülő drón, kerekes rover, többtengelyes robotkar vagy kétlábú humanoid). Az elsődlegesen támogatott fizikai test egy **Holybro X500 V2 drón PX4 repülésvezérlővel**, de a teljes UX/UI architektúrát úgy kell felépíteni, hogy az zökkenőmentesen skálázódjon más robotikai testekre is.

### A Biztonság-Kritikus UX Alapelvei

A robotika fizikai jelenléte miatt a rosszul megtervezett UX közvetlen anyagi kárt vagy életveszélyt okozhat. Ezért a ByteWolf felületein az **AI soha nem vezérelheti közvetlenül az aktuátorokat és motorokat**. A műveleti lánc végrehajtásának szigorú biztonsági modellje van, amelyet a felületnek transzparensen és félreérthetetlenül kell vizualizálnia:

```text
[ Felhasználói kérés (Natural Language / Térkép) ]
                       │
                       ▼
    [ AI által generált küldetésjavaslat ]
                       │
                       ▼
        [ Szabványosított MissionSpec ]
                       │
                       ▼
   [ SafetyGate Determinisztikus Ellenőrzés ] ──► (Elutasítás/Riasztás esetén hard stop)
                       │
                       ▼
         [ Emberi Jóváhagyás (Human-in-the-Loop) ]
                       │
                       ▼
       [ Robotikai Test Adapter (Body Adapter) ]
                       │
                       ▼
    [ PX4 vagy egyéb Robotvezérlő (Hardver) ]
```

**Kritikus UX Törvény:** A felhasználó egyetlen chatüzenettel vagy térképkattintással sem indíthatja el közvetlenül a robotot. A rendszernek minden esetben különálló fázisként kell kezelnie és vizuálisan elkülönítenie a **Tervezést (AI/MissionSpec/SafetyGate)**, a **Jóváhagyást (Operator Approval)** és a **Végrehajtást (Execution)**.

---

## 2. Információs Architektúra és Navigáció

### 2.1 Web sitemap (Desktop Control Room)
A desktop felület egy sűrű, de rendkívül átgondolt információs sűrűségű környezet, amely támogatja a többmonitoros elrendezéseket és a gyors billentyűparancsokat.

```text
[Control Room Portal]
 ├── 1. Fleet Dashboard (Robotválasztó és flotta-állapot)
 ├── 2. Active Control Station (Fő operátori felület)
 │    ├── 2.1 Live Telemetry Banner (Armed/Disarmed, Flight Mode, GNSS, Bat)
 │    ├── 2.2 Dual Camera Streams (Primary Front Camera & Downward Camera)
 │    │    └── 2.2.1 Object Detection Overlay (Confidence, Bounding Boxes, Tracker IDs)
 │    ├── 2.3 Interactive 3D Mission Map (Waypoints, Geofence, Home, Airspace)
 │    ├── 2.4 AI Cognitive Chat (NIM Planner, Tool outputs, Prompt Editor)
 │    └── 2.5 Quick Emergency Overlay (RTL, LAND, HOLD, Kill Switch)
 ├── 3. Mission Planner & Review Hub (Küldetés tervezése és jóváhagyása)
 │    ├── 3.1 MissionSpec Compiler (JSON nézet, útvonal-előnézet, SafetyGate verdikt)
 │    └── 3.2 Human Approval Screen (Kockázatelemzés, becsült energia, fallback akciók)
 ├── 4. Mission History & Replay (Történeti elemző felület)
 │    ├── 4.1 Flight Log Replay (Idővonal, telemetriai görbék, szenzor visszajátszás)
 │    └── 4.2 Evidence Manager (Észlelések, bizonyíték képek, AI Mission Summary)
 ├── 5. Semantic Memory Database (A kognitív rendszer memóriája)
 │    ├── 5.1 Personal Memory Facts (Felhasználói preferenciák, szokások)
 │    └── 5.2 World Model Claims (Környezeti észlelések, megerősített/bizonytalan objektumok)
 └── 6. Alerts & System Logs (Rendszerdiagnosztika és riasztások)
```

### 2.2 Mobil Navigációs Struktúra
A mobilalkalmazás a kültéri, egykezes használatra és a magas kontrasztra fókuszál. Egy 5-gombos alsó navigációs (Bottom Navigation) rendszert használ:

```text
[Bottom Navigation Bar]
 ├── TAB 1: ÁLLAPOT (Fleet / Telemetry)
 │    └── Felső kártyák: Élő telemetria, kapcsolat minőség, akkumulátor, vészleállító
 ├── TAB 2: KAMERA (Video Feed / Fullscreen Landscape Mode)
 │    └── Élő videó, észlelési keretek, kameraváltás (Front/Down), evidence snapshot gomb
 ├── TAB 3: KÜLDETÉS (Active Mission / Map Viewer)
 │    └── Valós idejű térkép, Waypoint követés, RTH és LAND vészgombok
 ├── TAB 4: CHAT (AI Copilot / Conversation Engine)
 │    └── Beszélgetés ByteWolffal, küldetés tervezési javaslatok kártyái, gyors jóváhagyás
 └── TAB 5: TOVÁBBIAK (Settings, Memory, Replay, Device Pairing)
      ├── Device Pairing & Auth (QR kódos párosítás az operátori konzollal)
      ├── Memory Fact-checks (Személyes memória szerkesztése)
      └── Alerts Log (Riasztási előzmények)
```

---

## 3. Fő Felhasználói Folyamatok (User Flows)

### Flow A — Természetes Nyelvű Küldetés (AI-native)
A másodlagos felhasználó vagy operátor természetes nyelven kiadott utasításának végrehajtási lánca:

1. **Bevitel:** A felhasználó beírja a chatbe: *"Repülj a kert végéhez, nézd meg, van-e ott valaki, majd gyere vissza."*
2. **AI Értelmezés:** A ByteWolf Cognitive Runtime (NVIDIA NIM) értelmezi a szándékot, és lebontja elemi navigációs lépésekre.
3. **Drafting:** A rendszer létrehoz egy `MissionSpec` tervezetet (TAKEOFF -> GOTO_LOCAL -> HOLD -> RTL).
4. **SafetyGate Futás:** A háttérben lefut a determinisztikus biztonsági ellenőrzés a `twin.yaml` korlátai alapján (magasság < 20m, távolság < 2000m, akkumulátor > 40%).
5. **Vizualizáció:** A chatben megjelenik egy **"Küldetésjavaslat" kártya**, amely sárga (Amber) szegéllyel jelzi, hogy jóváhagyásra vár. A térképen kirajzolódik a tervezett útvonal szaggatott vonallal.
6. **Emberi Jóváhagyás:** A felhasználónak rá kell kattintania a "Részletes ellenőrzés" gombra, ahol látja a SafetyGate pozitív verdiktjét, majd egy tudatos interakcióval (pl. "Slide to Approve" vagy kétlépcsős gomb) jóváhagyja a küldetést.
7. **Indítás:** A rendszer elküldi a `MissionSpec`-et a PX4-nek. A robot felszáll.
8. **Aktív Követés:** A felület "Aktív küldetés" nézetre vált. A térképen a szaggatott vonal folyamatossá (türkiz) válik, a drón ikonja mozog az útvonalon.
9. **Befejezés:** A drón sikeresen leszáll (LAND / RTH).
10. **Zárójelentés:** Megjelenik a Mission Summary kártya az AI által generált összefoglalóval és a rögzített bizonyítékokkal (evidence képek az észlelt személyekről).

### Flow B — Térképes Küldetés (Mission Planner)
Hagyományosabb, de AI-asszisztált térképes küldetéstervezés:

1. **Navigáció:** Az operátor a "Mission Planner" fülre lép.
2. **Kiválasztás:** Kiválasztja az aktív robotot (pl. *X500 Drone Alpha*).
3. **Kijelölés:** A 3D térképen rákattint egy célpontra, vagy kijelöl egy területet (Survey Area).
4. **Automatikus Tervezés:** Az AI kiszámítja az optimális útvonalat, figyelembe véve a World Modelben tárolt ismert akadályokat és a geofence határait.
5. **Paraméterezés:** A jobb oldali panelen az operátor finomhangolja a magasságot (pl. 5m) és a sebességet.
6. **Safety Gate Run:** A rendszer valós időben validál. Ha az operátor véletlenül a geofence-en kívülre helyez egy pontot, a felület azonnal pirosra vált, letiltja a jóváhagyást, és kiírja: *"Waypoint kívül esik a megengedett geofence területen!"*
7. **Jóváhagyás:** Ha minden paraméter zöld, az "Útvonal Jóváhagyása" gomb aktívvá válik. Megnyomásával elindul a küldetés.

### Flow C — Biztonsági Esemény (Safety Emergency)
Menet közben fellépő kritikus hiba kezelése:

1. **Triggel:** Repülés közben a drón akkumulátora 20% alá esik (Critical Low Battery), vagy megszakad a kapcsolat (Loss of Link).
2. **Riasztás:** A Control Room és a mobilalkalmazás azonnal **Emergency State**-be vált. A képernyő szélei pulzáló piros keretet kapnak, megszólal egy szaggatott figyelmeztető hangjelzés, és egy haptikus vibrációs mintázat fut le a mobilon.
3. **Transzparens Tájékoztatás:** Egy óriási, kitakarhatatlan modális ablak/kártya jelenik meg: *"KRITIKUS AKKUMULÁTOR SZINT: 18%. Kapcsolat megszakadási művelet (RTH) automatikusan aktiválva."*
4. **Automatikus Akció:** A felület mutatja, hogy a robot végrehajtja a `loss_of_link_action`-t (Return-to-Home). A térképen az aktuális pozíciótól a Home pontig tartó szakasz pirosra vált, jelezve a kényszer-visszatérést.
5. **Operátori Felülbírálat (Authorized Only):** Az operátornak felajánlja a felület a közvetlen "LAND NOW" (Azonnali leszállás ott, ahol van) vagy "HOLD" (Lebegés) magas szintű parancsokat, amelyek megerősítéséhez egy védelmi reteszt kell elhúzni.
6. **Audit és Replay:** A biztonságos földet érés után a rendszer lezárja a küldetést, és generál egy `v0.2` audit JSON-t. A felület felajánlja a "Riasztás elemzése és visszajátszás" gombot.

### Flow D — Mobil Küldetés Jóváhagyás (Mobile Approval)
Operátor távoli jóváhagyása külső kérésre:

1. **Push Értesítés:** Az operátor mobiljára értesítés érkezik: *"Új küldetés vár jóváhagyásra: Drón Alpha — Kert felderítése."*
2. **Hitelesítés:** Az értesítésre kattintva a rendszer FaceID / TouchID hitelesítést kér.
3. **Részletek:** Megnyílik a "Jóváhagyásra váró küldetés" képernyő. Az operátor látja a térképet a tervezett útvonallal, a magassági profilt és a becsült repülési időt.
4. **Kockázatértékelés:** A felület kiemeli a SafetyGate eredményt: *"SafetyGate: APPROVED. Maximális magasság: 12 m (Limit: 20 m). Szél sebessége: 4 m/s (Limit: 9 m/s)."*
5. **Döntés:** Az operátor elhúzza a képernyő alján lévő zöld csúszkát: **"Húzd el az indításhoz (Slide to Authorize)"**. (Ez megakadályozza a véletlen zsebben-jóváhagyást).
6. **Élő Követés:** A felület azonnal átvált az élő kamera és telemetria követésére.

---

## 4. Vizuális Design Koncepciók (Visual Directions)

Három különböző vizuális és funkcionális irányt vizsgáltunk meg a ByteWolf platformhoz:

### 1. irány: "Control Room" (Sűrű, Professzionális Operátori Nézet)
*   **Fókusz:** Maximális adat- és telemetria-sűrűség. Tökéletes a többmonitoros elrendezésekhez.
*   **Vizuális elemek:** Nagyon sötét háttér, finom rácsvonalak, kis betűméretek, sűrű információs panelek, flotta-szintű grafikonok, valós idejű telemetriai görbék.
*   **Hangulat:** Katonai irányítóközpont vagy NASA vezérlőterem, de modern, letisztult köntösben.

### 2. irány: "Calm Intelligence" (AI-First Minimalizmus)
*   **Fókusz:** "Az AI mindent elintéz, neked csak a lényeget kell látnod." Csökkenti a kognitív terhelést.
*   **Vizuális elemek:** Tágas terek, nagy tipográfia, minimalista kártyák, gyönyörű, elmosódott üveghatások (Glassmorphism), kevés, de rendkívül hangsúlyos állapotjelző. A felület magja egy intelligens beszélgetőablak (AI Chat) és egy kontextus-érzékeny térkép.
*   **Hangulat:** Prémium, futurisztikus, biztonságos, elegáns.

### 3. irány: "Field Operator" (Kültéri és Mobil Használatra Optimalizált)
*   **Fókusz:** Szélsőséges fizikai körülmények közötti olvashatóság, kesztyűs és egykezes használat támogatása.
*   **Vizuális elemek:** Ultra-magas kontraszt, vastag vonalak, óriási érintési célpontok (touch targets), élénk borostyán, sárga és piros állapotjelző színek, hangsúlyos ikonográfia szöveges címkékkel megerősítve.
*   **Hangulat:** Ipari célszerszám, robusztus és elnyűhetetlen.

### Kiválasztott Vizuális Irány és Indoklás: "Hybrid Calm Control"

A ByteWolf Robotics Platform számára a **"Hybrid Calm Control"** irányt választottuk ki.

**Indoklás:**
A platformnak egyszerre kell kiszolgálnia a magasan képzett robotikai operátorokat (akiknek szükségük van a mély telemetriára és a térképi Waypoint pontosságra) és az üzleti, kevésbé technikai felhasználókat (akik természetes nyelven akarnak kommunikálni a rendszerrel).
A tisztán "Control Room" túl ijesztő és zsúfolt lenne a másodlagos felhasználónak, míg a tisztán "Calm Intelligence" nem adna elég biztonságérzetet és részletességet egy krízishelyzetben lévő operátornak.

A **Hybrid Calm Control** ötvözi a két világ előnyeit:
1.  **AI-First Chat mag:** A bal/jobb oldali panelen mindig ott van a letisztult, intelligens asszisztens, ami emberi nyelvre fordítja a robotikai eseményeket.
2.  **Sűrű, de Strukturált Telemetria:** A képernyő tetején és a térkép felett fut egy professzionális, magas információs sűrűségű, de rendkívül tiszta hierarchiájú státuszsáv.
3.  **Környezettudatos sötét mód:** Alapértelmezetten egy nagyon mély kognitív sötétkéket és grafitot használunk, ami kíméli a szemet a sötét vezérlőteremben és a terepen éjszaka, de a magas kontrasztú elemeknek köszönhetően erős napsütésben is olvasható marad.

---

## 5. Design System Alapok (Design System Foundations)

### 5.1 Színrendszer (Color Palette)
Minden színnek funkcionális jelentése van. A színek használatát szigorúan korlátozzuk, hogy a riasztások azonnal kitűnjenek.

*   **Rendszer Alapszínek (Backgrounds & Surfaces):**
    *   `Deep Space` (Fő háttér): `#080B10` — Mély, majdnem fekete sötétkék. Csökkenti a kijelző fény kibocsátását kültéren.
    *   `Slate Surface` (Kártyák és panelek): `#121820` — Sötét grafitszürke, enyhén kékes árnyalattal.
    *   `Border Low` (Finom határolók): `#1E293B` — Finom rácsvonalak, inaktív állapotok.
    *   `Text High` (Elsődleges szöveg): `#F8FAFC` — Tiszta, törtfehér, maximális kontraszt.
    *   `Text Muted` (Másodlagos információ): `#94A3B8` — Kékes-szürke, kiegészítő adatok.

*   **Funkcionális Állapotszínek (Status Colors):**
    *   `Cyan Glow` (Normál működés / Aktív fázis / AI): `#06B6D4` vagy `#00F5FF` — Türkizkék. Biztonságot és high-tech precizitást sugároz.
    *   `Amber Warning` (Figyelmeztetés / Jóváhagyásra vár / Közepes riasztás): `#F59E0B` — Meleg borostyánsárga. Felhívja a figyelmet, de nem pánikoltat.
    *   `Red Alert` (Kritikus hiba / Vészhelyzet / SafetyGate Elutasítva): `#EF4444` — Élénkvörös. Azonnali beavatkozást igénylő állapot.
    *   `Information Blue` (Tájékoztatás / Rendszerüzenet): `#3B82F6` — Kobaltkék.
    *   `Offline Gray` (Kapcsolat nélkül / Ismeretlen): `#64748B` — Matt pala-szürke.

### 5.2 Tipográfia (Typography)
A betűtípusnak mérnöki pontosságúnak, de jól olvashatónak kell lennie. Olyan betűcsaládot használunk, amely támogatja a rögzített szélességű (tabular/monospaced) számokat, hogy a telemetriai adatok ne ugráljanak a képernyőn frissülés közben.

*   **Fő betűtípus:** `JetBrains Mono` (Telemetria, kódok, számok, gombok) és `Inter` (Szöveges leírások, AI chat, bekezdések).
*   **Hierarchia:**
    *   `Display Title` (Óriás telemetria): `JetBrains Mono`, Bold, 36px / Line Height: 1.2 (pl. Magasság és Sebesség értékek)
    *   `Header 1` (Főpanelek címei): `Inter`, Semi-Bold, 20px / Line Height: 1.4
    *   `Header 2` (Kártyacímek): `Inter`, Medium, 16px / Line Height: 1.4
    *   `Body Text` (Chat, leírások): `Inter`, Regular, 14px / Line Height: 1.5
    *   `Status Label` (Címkék, apró adatok): `JetBrains Mono`, Semi-Bold, 11px, All-caps / Line Height: 1.1

### 5.3 Rács és Spacing Rendszer (Grid & Spacing)
*   **Alap spacing egység:** 4px alapú rendszer (`4px`, `8px`, `12px`, `16px`, `24px`, `32px`, `48px`, `64px`).
*   **Belső panelek paddingje:** egységesen `16px` (Desktop) és `12px` (Mobile).
*   **Sarokkerekítések (Border Radius):**
    *   Kártyák és Panelek: `8px` — Szögletesebb, professzionálisabb mérnöki hatás.
    *   Státusz chipek és gombok: `4px` vagy `20px` (teljesen lekerekített pillér formátum).
*   **Árnyékok (Shadows):** Kerüljük az erős árnyékokat. Helyette finom, `1px` vastagságú belső vagy külső szegélyeket (borders) használunk ragyogó effektekkel (pl. cyan glow a kijelölt elemeken).

### 5.4 Desktop és Mobil Breakpointok
*   `Mobile`: 320px – 479px
*   `Tablet / Mobile Landscape`: 480px – 1023px (A tabletes operátori nézet 1024px-től skálázódik)
*   `Desktop Base`: 1024px – 1439px
*   `Desktop HD`: 1440px – 1919px
*   `Desktop Ultra-Wide`: 1920px felett (A felület három hasábosra nyílik: Flotta | Térkép & Kamera | AI Chat & Logs)

---

## 6. Komponenstár (Design System Component Library)

A platform konzisztenciáját az alábbi, előre megtervezett és specifikált komponensek biztosítják:

1.  **Robot Card (Robotkártya):**
    *   *Szerkezet:* Robot ikon (drón, rover, humanoid), név, állapot chip (Online, Active, Offline), akkumulátor ikon százalékkal, utolsó telemetria frissülés (stale indicator), aktuális küldetés neve.
    *   *Állapotok:* Hover, Selected (cyan border), Offline (szürke áttetszőség).

2.  **Telemetry Metric (Telemetriai mérőszám):**
    *   *Szerkezet:* Felső kisméretű címke (pl. "ALTITUDE"), nagy méretű érték rögzített szélességű betűtípussal (pl. "12.4 m"), alul apró grafikus trend-nyíl vagy változási sebesség (pl. "+0.5 m/s").

3.  **Status Chip (Státusz címke):**
    *   *Szerkezet:* Kis kapszula alakú elem háttérszínnel és szöveggel.
    *   *Variációk:* `ARMED` (piros pulzáló ponttal), `DISARMED` (zöld ponttal), `HOLD` (borostyán), `RTH` (kék), `MANUAL` (fehér).

4.  **Connection Indicator (Kapcsolat minőségjelző):**
    *   *Szerkezet:* Térerő ikon (4 pálcás) + késleltetés milliszekundumban (pl. "Latency: 42ms").
    *   *Viselkedés:* Ha a latency > 200ms, sárgára vált, ha > 500ms, pirosra és kiírja: "STALE TELEMETRY".

5.  **Alert Card (Riasztási kártya):**
    *   *Szerkezet:* Bal oldalon ikon (Warning / Critical), középen a hiba címe és a robot neve, alatta a javasolt lépés (pl. "Akkumulátor alacsony. RTL javasolt."), jobb oldalon az eltelt idő (pl. "2s").

6.  **Mission Progress (Küldetés haladási sáv):**
    *   *Szerkezet:* Vízszintes folyamatjelző, amely szakaszokra van osztva a MissionSpec lépései alapján (Takeoff -> Waypoint 1 -> Survey -> Landing). Az éppen futó szakasz türkizkéken pulzál, a befejezettek fix türkizkékek, a jövőbeliek szürkék.

7.  **Approval Card (Jóváhagyási kártya a chatben):**
    *   *Szerkezet:* Az AI által összeállított terv tömörített összefoglalója. Térkép-bélyegkép az útvonallal, SafetyGate pecsét ("PASSED"), becsült repülési idő és energiaigény. Két gomb: "Elutasítás" (piros szegélyes) és "Részletes ellenőrzés & Jóváhagyás" (teli türkiz).

8.  **Safety Verdict Component (SafetyGate Verdikt):**
    *   *Szerkezet:* Pajzs ikon, a SafetyGate döntése (APPROVED / REJECTED). Ha elutasított, felsorolja a megsértett szabályokat a `twin.yaml`-ből piros kiemeléssel (pl. *"Violated: Altitude 22m > Max 20m"*).

9.  **Camera Viewer (Kameranéző):**
    *   *Szerkezet:* Videó stream felület, jobb felső sarokban a kamera neve (pl. "PRIMARY FRONT Cam"), bal alsó sarokban az észlelések száma, jobb alsó sarokban teljes képernyős gomb és kameraváltó gyorsgomb.

10. **Detection Overlay (Észlelési maszk):**
    *   *Szerkezet:* Vékony, türkizkék szaggatott téglalap (Bounding Box) az észlelt tárgy körül. A sarokban apró címke: "Object: Human | Conf: 94% | ID: #042". Csak akkor látszik, ha az AI Perception modul aktív.

11. **Memory Fact Card (Memória tény kártya):**
    *   *Szerkezet:* Személyes információkat tartalmazó kártya a "Memory" fül alatt. Pl. *"Operátor preferált repülési magassága: 5m"*. Jobb szélén "Szerkesztés" és "Törlés" gombokkal.

12. **World Claim Card (Környezeti észlelési állítás):**
    *   *Szerkezet:* A robot által észlelt fizikai tények. Pl. *"Akadály észlelve: Kerítés"* koordinátákkal, frissességi idővel és megbízhatósági szinttel (Confidence: 89%). Állapotjelző címkéje: "Megerősített" (zöld) vagy "Bizonytalan" (borostyán).

---

## 7. Részletes Desktop és Mobil Képernyőtervek (ASCII Wireframes)

### 7.1 Desktop: Control Room (Sűrű Operátori Nézet)
Ez a nézet az aktív repülés és az operátori felügyelet központja. A képernyőt három fő hasábra osztjuk az optimális elrendezés érdekében:
1.  **Bal hasáb (300px):** Fleet & Active Robot státusz, részletes telemetria listák, eseménynapló.
2.  **Középső hasáb (Flex):** 3D Térkép és alatta a két Kameraélő stream.
3.  **Jobb hasáb (400px):** ByteWolf AI Chat és a Mission Review / Approval felület.

#### Desktop Control Room Drótváz (1440px)

```text
+-----------------------------------------------------------------------------------------------------------------------+
|  BYTEWOLF ROBOTICS   [FLEET]   [CONTROL ROOM]   [MISSION PLANNED]   [SEMANTIC MEMORY]             | 2026-07-21 14:32:01  |
+-----------------------------------------------------------------------------------------------------------------------+
| ACTIVE: X500 DRONE ALPHA   | STATE: AIRBORNE (FLYING) | MODE: WAYPOINT | BAT: 74% [|||||||..] | GNSS: 3D LOCK (18 SVs) |
+----------------------------+------------------------------------------+-----------------------+-----------------------+
| [L1] FLEET & TELEMETRY     | [L2] INTERACTIVE 3D MISSION MAP & PERCEPTION OVERLAY             | [L3] COGNITIVE AI CHAT |
|----------------------------|------------------------------------------------------------------|-----------------------|
| > X500 Drone Alpha [ACTIVE]| +--------------------------------------------------------------+ | USER >                |
|   Bat: 74% | Alt: 12.4m    | | [H] HOME (0,0)                                               | | Repülj a kert       |
| > Rover Beta       [READY] | |                                                              | | végéhez, nézd meg,  |
| > Humanoid Lab     [OFFLINE]| |                      *(Drón Alpha)                           | | van-e ott valaki,   |
|                            | |                     .  \                                     | | majd gyere vissza.  |
|-- DETAILED METRICS --------| |                    .    \                                    | |                     |
| ALTITUDE      SPEED        | |                   .      \                                   | | BYTEWOLF AI >       |
| 12.4 m        2.4 m/s      | |                  .        \ [WP1] 12m N, 5m E                | | Értelmeztem a       |
| [Trend: Up]   [Trend: Stable]| |                 .          * (Target Area)                 | | kérését. Terv       |
|                            | |                .                                             | | elkészült.          |
| BATTERY V     CURRENT      | |     - - - - - . (Planned Return)                             | |                     |
| 15.4 V        8.2 A        | |    |          |                                              | |+-- MISSION SPEC ----|
|                            | |    | Geofence |  [X] Measured Obstacle (Tree)                | || Spec ID: ms_0428   ||
| WP DISTANCE   TOTAL DIST   | |     - - - - - -                                              | || SafetyGate: PASSED ||
| 4.2 m         48.2 m       | +--------------------------------------------------------------+ || Alt: 12m | Rad: 15m||
|                            |------------------------------------------------------------------|| Est. Energy: 12%    ||
|-- EVENT LOG ---------------| [L2.2] CAMERA FEED 1 (FRONT)      | [L2.3] CAMERA FEED 2 (DOWN)  ||--------------------||
| 14:30:12 Armed & Taken off | +-------------------------------+ +----------------------------+|| [ REJECT ] [APPROVE]||
| 14:31:05 Waypoint 1 reached| | Object Detection: Active      | | Nav Marker: Visible        | |+--------------------+|
| 14:31:45 Target Area scan  | | [Human 94% ID#12]             | | [H]                        | |                     |
| 14:32:00 [ALERT] Obstacle  | | +---------+                   | |                            | | Message ByteWolf... |
|           detected (12m E) | | |    *    |                   | |                            | | [Send] [Microphone] |
+----------------------------+---------------------------------+--------------------------------+-----------------------+
| EMERGENCY OVERRIDES:       | [ HOLD (Space) ]   [ RETURN TO HOME (H) ]   [ LAND (L) ]   | [!!! EMERGENCY KILL !!!]|
+-----------------------------------------------------------------------------------------------------------------------+
```

---

### 7.2 Camera & Perception Screen (Desktop Detalizált Kamera és Észlelési Képernyő)
Az operátor átválthat teljes képernyős kamera nézetre. Ebben az esetben az élő kép a domináns, de a legfontosabb repülésbiztonsági információk (Heads-Up Display - HUD stílusban, rendkívül letisztultan) továpra is láthatóak maradnak a képernyő szélein overlayként.

#### Kamera és Perception Nézet Drótváz

```text
+-----------------------------------------------------------------------------------------------------------------------+
| Cam: FRONT_RGB_1080P | Resolution: 1920x1080 @ 30fps | Latency: 42ms | Frame Drops: 0% | Stream: OK                  |
+-----------------------------------------------------------------------------------------------------------------------+
|  [AR] ARMED  |  ALT: 12.4 m  |  SPD: 2.4 m/s  |  BAT: 74%  |  GNSS: 3D LOCK  |  GEOFENCE: OK  |  [ RTH (Quick Bind) ] |
|-----------------------------------------------------------------------------------------------------------------------|
|                                                                                                                       |
|    [ Detection Overlay Active - Model: YOLOv8_WolfEye_v1.4 ]                                                          |
|                                                                                                                       |
|         +------------------------------------+                                                                        |
|         | [Human #042]                       |                                                                        |
|         | Confidence: 94.2%                  |                                                                        |
|         | Track ID: TRK_8820                 |                                                                        |
|         | State: Moving West                 |                                                                        |
|         |                                    |                                                                        |
|         |               x (Center)           |                                                                        |
|         |                                    |                                                                        |
|         |                                    |                                                                        |
|         |                                    |                                                                        |
|         +------------------------------------+                                                                        |
|                                                                                                                       |
|                                                                +---------------------------------------+              |
|                                                                | EVIDENCE INFO                         |              |
|                                                                |---------------------------------------|              |
|                                                                | Keyframe Captured: Yes                |              |
|                                                                | Source: front_rgb                     |              |
|                                                                | Object Class: Human                   |              |
|                                                                | Confidence: 94%                       |              |
|                                                                | [ Capture Snapshot ] [ Flag Evidence] |              |
|                                                                +---------------------------------------+              |
|                                                                                                                       |
+-----------------------------------------------------------------------------------------------------------------------+
| [Switch to Down Camera]      [Toggle Bounding Boxes]      [Model Settings]      [Exit Fullscreen (Esc)]              |
+-----------------------------------------------------------------------------------------------------------------------+
```

---

### 7.3 AI Chat & Mission Review/Approval Panel (Desktop)
A jobb oldali AI Chat és a Mission Review panelek részletes kialakítása. Kiemeli a szoftveres SafetyGate determinisztikus ellenőrzését és az explicit, tudatos operátori jóváhagyást.

#### AI Chat és Jóváhagyás Drótváz

```text
+----------------------------------------+
| COGNITIVE ASSISTANT (NIM AGENT v0.1)   |
+----------------------------------------+
| Chat Session: Durable Local #8821      |
|----------------------------------------|
| ByteWolf > Üdvözlöm! Drón Alpha készen  |
| áll. Mit szeretne végrehajtani?        |
|                                        |
| User > Repülj 5 méter magasra, majd    |
| menj 10 métert északra, és szállj le.  |
|                                        |
| ByteWolf > Megértettem a parancsot.    |
| Elkészítettem az útvonaltervet és      |
| lefutattam a SafetyGate ellenőrzést.   |
|                                        |
| +-- KÜLDETÉS JAVASLAT: ms_9921 --------+|
| |                                      ||
| | Cél: Takeoff -> Goto -> Land         ||
| | Waypoints:                           ||
| |  1. TAKEOFF (Alt: 5.0m)              ||
| |  2. GOTO (North: 10m, East: 0m)      ||
| |  3. LAND (Alt: 0m)                   ||
| |                                      ||
| | SafetyGate: APPROVED  [ Pajzs Ikon ] ||
| |  - Max Altitude: 5.0m (Limit: 20.0m) ||
| |  - Distance: 10.0m (Limit: 2000.0m)  ||
| |  - Battery Reserve: 74% (Min: 40%)   ||
| |                                      ||
| | Kockázatok: Nincs észlelt akadály    ||
| | Várható energiaigény: 4% akkumulátor  ||
| |                                      ||
| | [ ELUTASÍTÁS ]   [ JÓVÁHAGYÁS ]      ||
| +--------------------------------------+ |
|                                        |
| [ Message ByteWolf...                ] |
+----------------------------------------+
```

---

### 7.4 Mobilalkalmazás Képernyők (Native Mobile Screens)

A mobil felületeknek extrém kontrasztosnak kell lenniük, minimálisan 44x44 pixeles érintési területekkel (touch targets), és egykezes használatot támogató gombokkal a képernyő alsó felén.

#### Mobil: Fő Állapot & Élő Telemetria (Tab 1)

```text
+----------------------------------------+
| 14:32  Drón Alpha         [|||||] 74%  |
+----------------------------------------+
| KAPCSOLAT: KIVÁLÓ (Latency: 42ms)      |
| GEOFENCE ÁLLAPOT: BIZTONSÁGOS          |
+----------------------------------------|
|                                        |
|       STATE: AIRBORNE (FLYING)         |
|       ------------------------         |
|                                        |
|   MAGASSÁG (ALT)       SEBESSÉG (SPD)  |
|     12.4 m               2.4 m/s       |
|                                        |
|   AKKUMULÁTOR (BAT)    MŰKÖDÉSI MÓD    |
|       74%                 WAYPOINT     |
|                                        |
|----------------------------------------|
| LEGFONTOSABB UTOLSÓ AKTIVITÁSOK        |
| - 14:31 Waypoint 1 elérve              |
| - 14:30 Felszállás jóváhagyva          |
|                                        |
|----------------------------------------|
| [!] EMERGENCY OVERRIDES (TAP & HOLD)   |
| +------------------------------------+ |
| |        [  RETURN TO HOME  ]        | |
| +------------------------------------+ |
| |        [    LAND NOW   ]           | |
| +------------------------------------+ |
+----------------------------------------+
| [STÁTUSZ] [KAMERA] [TÉRKÉP] [CHAT] [MÉG]|  <-- Bottom Navigation Bar
+----------------------------------------+
```

---

#### Mobil: Távoli Küldetés Jóváhagyás (Push értesítés után)

```text
+----------------------------------------+
| 14:32  KÜLDETÉS JÓVÁHAGYÁSA            |
+----------------------------------------+
| Robot: X500 DRONE ALPHA                |
| Kérő: Rendszer (User: Kovács Péter)    |
|----------------------------------------|
| UTASÍTÁS:                              |
| "Kert felderítése és személykeresés"   |
|                                        |
| TERVEZETT PARAMÉTEREK:                 |
| Max magasság: 12.0 m                   |
| Max távolság: 15.0 m                   |
| Várható repülési idő: 2 p 15 mp        |
| Becsült energia: 12% akkumulátor       |
|                                        |
| SAFETYGATE VERDIKT:                   |
| APPROVED [ Pajzs Ikon ]                |
| - Minden tervezett pont geofence-en    |
|   belül van.                           |
| - Akkumulátor (74%) elegendő a         |
|   visszatéréshez (RTH).                |
|                                        |
| Fallback akció: RTL (Return-to-Home)   |
|----------------------------------------|
| [X] MEGÉRTETTEM ÉS ELFOGADOM A         |
|     REPÜLÉSI KOCKÁZATOKAT.             |
|                                        |
|      >>> SLIDE TO AUTHORIZE >>>        |
|      [=======>                ]        |
|                                        |
+----------------------------------------+
| [ Elutasítás ]      [ Kapcsolatfelv. ] |
+----------------------------------------+
```

---

## 8. Biztonságkritikus UX-állapotok (Safety-Critical States)

A biztonság-kritikus állapotok vizuális visszajelzése soha nem alapulhat kizárólag a színeken (színvakok és extrém kültéri fényviszonyok támogatása). Minden állapotnak egyedi **ikont**, **szöveges státuszcímkét** és **vizuális textúrát / viselkedést** kell kapnia.

| Biztonság-kritikus állapot | Vizuális Szín | Ikon és Státusz Címke | Vizuális Hatás / Viselkedés a Felületen |
| --- | --- | --- | --- |
| **Nincs Kapcsolat (No Connection)** | Offline Szürke | `[!] DISCONNECTED` | A teljes képernyő kap egy féligáttetsző szürke maszkot, rajta egy villogó figyelmeztető ablakkal: *"Nincs kapcsolat a robottal. Telemetria leállt."* Minden indító gomb inaktívvá válik. |
| **Kapcsolat Helyreállítása (Reconnecting)** | Amber Sárga | `[~] RECONNECTING...` | Pulzáló sárga körkörös betöltő animáció (loading spinner). A korábbi adatok elhalványulnak (50% opacity), jelezve, hogy stale adatokról van szó. |
| **Elavult Telemetria (Stale Telemetry)** | Amber Sárga | `[?] STALE DATA (2.5s)` | A telemetria számai mellett megjelenik egy kis sárga kérdőjel és a legutóbbi sikeres adat óta eltelt másodpercek száma. Ha az idő > 3 mp, a parancsküldés gombok azonnal letiltásra kerülnek. |
| **Kamera Offline (Camera Offline)** | Sötétszürke | `[X] CAMERA OFFLINE` | A videó stream helyén egy statikus, zajmintás (analog static texture) szürke háttér jelenik meg áthúzott kamera ikonnal. A repülési telemetria ettől még látható és aktív marad. |
| **Alacsony Akkumulátor (Low Battery)** | Amber Sárga | `[!] LOW BATTERY (28%)` | Az akkumulátor ikon sárgán villog (1 Hz frekvenciával). A chatben megjelenik egy automatikus figyelmeztetés: *"Akkumulátor a figyelmeztetési limit alatt. RTL javasolt."* |
| **Kritikusan Alacsony Akku (Critical Low Bat)** | Piros | `[!!!] BATTERY CRITICAL (18%)` | A képernyő teljes kerete kap egy pirosan villogó (2 Hz frekvenciájú) 4px-es szegélyt. Hangjelzés és mobil vibráció indul el. Felugrik a kitakarhatatlan RTH / LAND Emergency Action Sheet. |
| **Bizonytalan GNSS (Uncertain GNSS)** | Piros / Sárga | `[?] POOR GPS LOCK` | A térerő ikon sárgára vagy pirosra vált, mellette a HDOP érték pirossal kiemelve (pl. `HDOP: 2.8`). A Waypoint-alapú indítás gomb elhomályosul és kattintáskor kiírja: *"Felszállás tiltva a gyenge GPS pozíció miatt."* |
| **SafetyGate Elutasítás (SafetyGate Rejected)**| Piros | `[X] SAFETY REJECTED` | A küldetésjavaslat kártya piros szegélyt kap, a gombok helyett egy piros üzenet jelenik meg a hiba okával (pl. *"Magasság limit megsértve (25m > 20m)"*). A jóváhagyási gomb fizikailag letiltottá válik. |
| **Return-to-Home Aktív (RTH Active)** | Kobaltkék | `[RTH] RETURNING TO LAUNCH`| A képernyő tetején lévő státuszsáv kéken pulzál. A térképen a repülő ikon irányvektora automatikusan a Home pontra áll be, az útvonalat kék vonallá színezi a rendszer. |
| **Kényszerleszállás (Emergency Land)** | Piros | `[!] LANDING NOW` | Piros, folyamatosan villogó státuszjelző. A felületen a "HOLD" és "RTH" gombok inaktívvá válnak, csak a fizikai leszállás állapotát követő telemetria és a kamera marad aktív. |

---

## 9. Szemantikus Memória és Világmodell Elválasztása
A ByteWolf biztonságos működésének alapfeltétele, hogy a **Személyes Memóriát** (a felhasználó szubjektív preferenciái, szokásai) és a robot **Világmodelljét** (objektív, fizikai mérések, észlelt akadályok és objektumok) szigorúan különválasszuk vizuálisan és fogalmilag is.

```text
+-----------------------------------------------------------------------------+
|                            COGNITIVE DATA ENGINE                            |
+-----------------------------------------------------------------------------+
|  [ PERSONAL OPERATOR MEMORY ]             |  [ ROBOT WORLD MODEL ]          |
|  (Személyes preferenciák, korábbi utasítások)|  (Fizikai észlelések, akadályok) |
|-------------------------------------------|---------------------------------|
| - Operátor neve: Kovács Péter             | - Ismert akadály #1: Fa         |
|   (Forrás: Profil beállítások)            |   Koordináta: N12.4, E5.1       |
| - Kedvelt repülési magasság: 5.0m         |   Confidence: 98% [Megerősített] |
|   (Forrás: Korábbi repülési statisztikák) | - Észlelt objektum #42: Ember   |
| - Geofence zóna elnevezése: "Kert"        |   Koordináta: N15.0, E12.3      |
|   (Forrás: Chat bejegyzés, 2026-07-15)    |   Confidence: 54% [Bizonytalan] |
+-----------------------------------------------------------------------------+
```

### Vizuális Különbségek:
1.  **Személyes Memória (Memory Facts):**
    *   *Stílus:* Lágy kék és szürke tónusok, dokumentum és felhasználó ikonok.
    *   *Műveletek:* Mindig közvetlenül szerkeszthetők, törölhetők vagy elfelejthetők (GDPR és operátori kontroll) egyetlen kattintással.
2.  **Világmodell (World Claims):**
    *   *Stílus:* Technikai rácsos elrendezés, radar és térkép koordináták, megbízhatósági szintet jelző százalékos csúszka (Confidence Indicator).
    *   *Műveletek:* Nem törölhetők közvetlenül kézzel (mivel fizikai méréseken alapulnak), de kaphatnak "Felülvizsgált" vagy "Érvénytelenített" státuszt az operátor által. Az állítások állapotát színekkel is megerősítjük: Megerősített (Zöld), Valószínű (Türkiz), Bizonytalan (Borostyán), Ellentmondásos (Piros), Elavult (Szürke).

---

## 10. Térképi Jelölések és Küldetéstervezési Szabályok (Map Representation)

A 3D Térkép (Mapbox GL JS / WebGL alapú) a legfontosabb navigációs elem. Itt a legszigorúbb szabályok érvényesek a fizikai biztonság vizualizációjára:

1.  **Waypointok és Útvonal:**
    *   *Aktív Waypoint:* Türkizkék pulzáló kör, közepén a waypoint sorszámával.
    *   *Tervezett Útvonal:* Szaggatott türkiz vonal.
    *   *Végrehajtás alatti útvonal:* Folyamatos, izzó türkizkék vonal.
2.  **Geofence és Határok:**
    *   *Allowed Geofence:* Egyértelműen meghúzott, vörös színű, de áttetsző vörös kitöltésű határoló poligon. A megengedett repülési területen kívüli rész sötétített maszkot kap, jelezve, hogy oda parancs nem küldhető.
    *   *Max Működési Sugár:* Egy halvány kék koncentrikus kör a Home pozíció körül, amely a `max_radius_m` (2000 m) határát jelzi.
3.  **Légtér Biztonsági Állapotok (Airspace Visuals):**
    *   **Mért Akadály (Measured Obstacle):** Piros kitöltésű 3D hasáb vagy henger (pl. észlelt fa vagy épület).
    *   **Ismert Szabad Terület (Known Free Space):** Halvány zöldes árnyalatú, rácsos textúra.
    *   **Nem Megfigyelt Terület (Unobserved Area):** Szürke ködfátyol (Fog of War) jellegű textúra.
    *   *UX Törvény:* A felület soha nem jelenítheti meg a nem megfigyelt területet zöldnek vagy biztonságosnak. Mindig fel kell hívnia a figyelmet arra, hogy az ismeretlen terület potenciális kockázatot hordoz!

---

## 11. Responsive Breakpointok és Interakciós Tervek

### 11.1 Átmenetek és Animációk (Micro-interactions)
Minden animációnak funkcionális célt kell szolgálnia, csökkentve a felhasználó kognitív terhelését. Kerüljük az öncélú játékos HUD animációkat.

*   **Belépési / Kilépési animációk (Framer Motion / Motion):**
    *   A felugró modális ablakok és vészhelyzeti figyelmeztetések `AnimatePresence` használatával lépnek be (Opacity: 0 -> 1, Scale: 0.95 -> 1, Duration: 150ms, Ease: `easeOut`).
    *   A chat üzenetek alulról felfelé csúsznak be finom áttűnéssel (Y: 10px -> 0px, Duration: 200ms).
*   **Státuszváltások:**
    *   Amikor a robot állapota `DISARMED`-ről `ARMED`-re vált, a státusz chip háttérszíne egy gyors vörös villanással (pulse flash, Duration: 300ms) hívja fel magára a figyelmet, majd stabil piros pulzálássá alakul.
*   **Akadály észlelése a térképen:**
    *   Új akadály észlelésekor a térképen lévő marker három gyors piros koncentrikus gyűrűvel (ping radar animáció, Duration: 1.5s) hívja fel magára az operátor figyelmét.

### 11.2 Akadálymentesség (Accessibility / WCAG AA)
A ByteWolf felületét úgy kell megtervezni, hogy az megfeleljen a WCAG 2.1 AA akadálymentességi szintnek:

*   **Kontrasztarány:** A szöveges elemek kontrasztaránya a sötét háttér előtt legalább `4.5:1` (Display Title és vastag betűk esetén `3:1`). A JetBrains Mono betűtípus gondoskodik a karakterek tiszta elkülönüléséről.
*   **Billentyűzetes Navigáció:** Minden lényeges operátori művelet elérhető gyorsbillentyűkkel is (Space: HOLD, H: Return-to-Home, L: Land, Esc: Kilépés a teljes képernyős nézetből). A fókuszállapotot egy vékony, ragyogó türkizkék keret jelzi az aktív komponens körül.
*   **Mobil Érintési Célpontok (Touch Targets):** Mobilon minden gomb és interaktív elem mérete minimum `44x44px`, egymástól legalább `8px` távolságra elhelyezve, megelőzve a mellényomásokat kesztyűben vagy rázkódó járműben történő használat során.
*   **Színvak Támogatás:** Minden szín alapú állapotjelzést kiegészít egy egyedi ikon és egy egyértelmű szöveges leírás is.

---

## 12. Fejlesztői Handoff Specifikáció (Developer Handoff Guide)

A frontend fejlesztőcsapat számára az alábbi megvalósítási irányelveket határozzuk meg:

### Tech Stack Ajánlás:
*   **Web Dashboard:** React 18+ / Next.js (App Router), Tailwind CSS (a v3 selector alapú sötét mód stratégiával), Mapbox GL JS a 3D térképhez, Lucide React az ikonokhoz, és Framer Motion az animációkhoz.
*   **Mobile App:** React Native (Expo) vagy Flutter, megőrizve a megosztott Tailwind-szerű stylingot és komponens logikát.

### Tailwind CSS Konfigurációs Minta (`tailwind.config.js`):

```javascript
module.exports = {
  darkMode: 'selector',
  theme: {
    extend: {
      colors: {
        bytewolf: {
          space: '#080B10',       // Deep Space Háttér
          slate: '#121820',       // Surface Kártya
          border: '#1E293B',      // Border Low
          cyan: '#06B6D4',        // Cyan Glow (Normál / AI)
          amber: '#F59E0B',       // Amber Warning (Figyelmeztetés)
          red: '#EF4444',         // Red Alert (Kritikus)
          blue: '#3B82F6',        // Info Blue
          textHigh: '#F8FAFC',    // Elsődleges szöveg
          textMuted: '#94A3B8',   // Másodlagos szöveg
        }
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'monospace'],
        sans: ['Inter', 'sans-serif'],
      },
      animation: {
        'pulse-fast': 'pulse 1s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'radar-ping': 'ping 1.5s cubic-bezier(0, 0, 0.2, 1) infinite',
      }
    }
  }
}
```

### JSON v0.2 Audit Log Integrációs Pontok:
Az elkészült felületeknek minden küldetés lezárásakor be kell olvasniuk és meg kell jeleníteniük az audit JSON fájlokat.
A `brain/mission/artifacts.py` által kiírt fájlok szerkezete az alábbi kulcsokat tartalmazza, amelyeket a felületen vizualizálni kell:
*   `mission_id`: Egyedi azonosító (pl. `ms_0428`)
*   `state_transitions`: Az állapotváltozások pontos időbélyegei (arming -> taking_off -> hovering -> landing -> completed). Ezt egy vízszintes esemény-idővonalon kell kirajzolni.
*   `safety_decision`: A SafetyGate verdiktje és a vizsgált határértékek.
*   `preflight_snapshot`: A repülés előtti akkumulátor és GPS pozíció állapota.

---

### Összegzés és Jóváhagyás

Ez az architektúra és design rendszer dokumentum garantálja, hogy a **ByteWolf Robotics Platform** felületei nem csupán esztétikusak és modernek lesznek, hanem megfelelnek a legszigorúbb repülésbiztonsági előírásoknak is. Az egyértelműen elkülönített kognitív beszélgetőtér és a valós idejű telemetriát felügyelő operátori modulok tökéletes szinergiában valósítják meg a **"One Brain. Many Bodies"** jövőképét.
