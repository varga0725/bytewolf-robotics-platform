# ByteWolf publikus marketing web — implementálható terv

**Státusz:** tervezési javaslat
**Célközönség:** robotikai és AI fejlesztők, integrációs partnerek, technikai döntéshozók, potenciális ügyfelek és befektetők
**Alapdokumentumok:** `docs/bytewolf_ux_ui_architecture.md`; Notion: *06 — Marketing Website Architecture*
**Termékállítás:** *One Brain. Many Bodies.*

## 1. Cél és termékpozicionálás

A publikus weboldal nem operátori termékfelület és nem puszta portfólió. Három állítást kell rövid, ellenőrizhető formában igazolnia:

1. A ByteWolf világos termékvíziót képvisel: egy testfüggetlen kognitív réteg több robotikai embodiment számára.
2. A vízió mögött tényleges mérnöki rendszer áll: Cognitive Runtime, MissionSpec, determinisztikus SafetyGate, digital twin és body adapterek.
3. A fejlesztés állapota folyamatosan, bizonyítékokkal követhető; a működő X500 drón az első aktív embodiment, a rover és humanoid nem kész termékként jelenik meg.

### Fő üzenet és bizonyítási sorrend

- **Kategória (0–5 mp):** „AI-native, governed autonomy platform for robots.” Magyar környezetben: „AI-native, biztonságosan felügyelt autonómia robotokhoz.”
- **Megkülönböztetés:** a kognitív intelligencia nem egyetlen hardverhez kötött; a rendszer egyetlen, szabványosított útvonalon alakítja a szándékot ellenőrizhető küldetéssé.
- **Biztonsági bizonyíték:** az AI küldetést javasol, a SafetyGate validál, az ember jóváhagy; a motorvezérlés nem a marketingoldal és nem az AI felelőssége.
- **Aktuális bizonyíték:** X500/PX4/Gazebo digital twin, teszteredmény, videó vagy képernyőkép, egyértelmű korlátozás és következő lépés.

Minden termék- és technológiaállításnál kötelező állapotcímke: **Aktív**, **Kísérleti**, **Tervezett** vagy **Vízió**. Ez megakadályozza, hogy roadmap-elem kész képességnek tűnjön.

## 2. Információs architektúra

### Fő navigáció

| Navigáció | Feladata | Elsődleges cél |
| --- | --- | --- |
| Platform | A rendszerérték és a vezérelt autonómia bemutatása | Platform oldal |
| Technology | Cognitive Runtime, safety és digital twin mélyebb magyarázata | Technology landing / aloldalak |
| Embodiments | Az azonos „agy” és több test kapcsolatának bemutatása | Embodiments oldal |
| Developers | Integrációs és fejlesztői belépési pont | Developers oldal |
| Build Log | Dátumozott, bizonyíték-alapú előrehaladás | Build Log index |
| Company | Küldetés, csapat, kapcsolat | About / Contact |

### URL-térkép és oldalkontraktus

| Útvonal | Cél | Kötelező tartalom | Elsődleges CTA |
| --- | --- | --- | --- |
| `/` | Kategória és hitelesség azonnali közlése | Hero, X500 bizonyíték, architektúra-folyam, safety, build-log előnézet | „Explore the platform” |
| `/platform` | End-to-end platformnarratíva | Perception → Memory → Reasoning → Mission Proposal → Safety Validation → Body Execution → Feedback; állapotcímkék | „View architecture” |
| `/technology/cognitive-runtime` | Kognitív réteg magyarázata | természetes nyelv, memória, érvelés, mission proposal; határok | „Read the architecture” |
| `/technology/safety-governance` | Bizalomépítés, pontos biztonsági modell | SafetyGate, Human-in-the-Loop, audit trail, „AI nem vezérel közvetlenül aktuátort” | „See safety model” |
| `/technology/digital-twin` | A validáció és szimuláció bemutatása | PX4/Gazebo, mérési bizonyítékok, ismert korlátok | „Read build evidence” |
| `/embodiments` | Ugyanazon runtime több testre való alkalmazhatósága | X500 aktív, Rover tervezett, Humanoid vízió; adaptermodell | „Explore embodiments” |
| `/embodiments/x500-drone` | Első működő embodiment bizonyítása | architektúra, demo, safety envelope, teszt- és korlátkártyák | „View build log” |
| `/embodiments/rover` | Roadmap kommunikáció | csak tervezett scope, függőségek, nincs teljesítményígéret | „Follow the build” |
| `/embodiments/humanoid` | Hosszú távú vízió | alapelv, kutatási hipotézisek, „Vízió” jelölés | „Read the vision” |
| `/developers` | Developer/partner érdeklődés konverziója | SDK és plugin modell, body adapter interface, MissionSpec példa, API státusz, GitHub/docs link | „Developer preview” / „View docs” |
| `/docs` | Kurált technikai dokumentációs belépő | architektúra, biztonság, integráció, státusz szerinti linkek | „Open documentation” |
| `/build-log` és `/build-log/[slug]` | Bizonyítékok, változásnapló és hitelesség | dátum, milestone/release, evidence, korlátok, következő lépés | „Follow updates” |
| `/about`, `/contact` | Kapcsolat és vállalati kontextus | küldetés, rövid csapat-/cégbemutató, kapcsolat | „Start a conversation” |
| `/privacy`, `/legal` | Jogi megfelelés | adatkezelés, jogi információ | — |

Az első kiadásban a `/docs` a meglévő dokumentáció kurált indexe vagy külső hivatkozása lehet; nem szükséges teljes dokumentációs portálként megvalósítani.

## 3. Tartalom- és CTA-stratégia

### Homepage történetíve

1. **Hero:** rövid kategória-meghatározás, a *One Brain. Many Bodies.* ígéret, elsődleges és másodlagos CTA.
2. **Bizonyítékblokk:** az X500 mint „Aktív embodiment”, egy konkrét teszt-/demo-asset és a hozzá tartozó korlátlink.
3. **Platformfolyam:** a hétlépéses Perception–Feedback folyamat; a Safety Validation és Human Approval vizuálisan elkülönül a Body Executiontől.
4. **Safety & governance:** a felelősségi határok rövid, közérthető magyarázata, a részletekhez vezető CTA-val.
5. **Embodiment-kártyák:** X500 / Rover / Humanoid állapotjelöléssel; ez nem termékkatalógus.
6. **Build Log előnézet:** három friss bejegyzés dátummal, bizonyítéktípussal és állapotcímkével.
7. **Konverziós zárás:** fejlesztői előnézet vagy partneri kapcsolat; a felhasználó szándéka szerint két külön CTA.

### CTA-hierarchia

- **Elsődleges:** „Explore the platform” — a platformoldalra visz, nem értékesítési űrlapra.
- **Technikai:** „View architecture”, „Read the safety model”, „Open documentation”, „View build evidence”.
- **Konverziós:** „Request developer preview” és „Talk to us”. Ezek csak világos beleegyezéssel gyűjtenek adatot, és a céljukat az űrlap mellett is leírják.
- **Követés:** „Follow the build” — későbbi newsletter/RSS integráció előkészített helye; az első slice-ban lehet egyszerű Build Log link.

A CTA-k sosem sugallhatják, hogy a látogató robotot indíthat, drónt vezérelhet, vagy operátori jogosultságot szerez. A publikus web kizárólag tájékoztatási és érdeklődésgyűjtési felület.

### Build Log bejegyzés-séma

Minden bejegyzés kötelező mezői: cím, dátum, milestone vagy release, rövid összefoglaló, legalább egy bizonyíték (videó, screenshot, mérési vagy teszteredmény), ismert korlátozások, következő lépés, kapcsolódó dokumentáció. A bizonyíték szintjét explicit címke jelzi: `unit/contract`, `app+SITL` vagy `PX4/Gazebo fault-injection`.

## 4. Desktop-first design nyelv

### Irány: „Calm Proof, Hybrid Control”

A marketing web a jóváhagyott **Hybrid Calm Control** rendszer publikus, légiesebb változata. Nem másolja a sűrű Control Roomot: annak vizuális jelzéseit a hitelesség és a rendszerérthetőség szolgálatába állítja.

- **Alapfelület:** `Deep Space #080B10`; emelt kártyák: `Slate Surface #121820`; finom határok: `#1E293B`.
- **Tipográfia:** Inter a narratív szöveghez; JetBrains Mono a technikai állapotokhoz, mérőszámokhoz, komponenscímkékhez és kódrészletekhez. A monospace számok tabulárisak.
- **Színjelentés:** cyan (`#06B6D4`) csak aktív/AI/kiemelt architektúrához; amber (`#F59E0B`) figyelmeztetéshez vagy jóváhagyásra váró állapothoz; red (`#EF4444`) kizárólag kritikus elutasításhoz; offline gray (`#64748B`) inaktív vagy bizonytalan állapothoz. A szín soha nem az egyetlen állapotjel.
- **Rács:** desktop base 12 oszlop, maximum 1440 px tartalomszélesség, 24 px gutter; 1440 px felett a sávok tágulhatnak, de a szöveges olvasósáv 720 px körül marad. 4 px-es spacing skála, kártyákban jellemzően 24 px belső tér.
- **Kompozíció:** nagy tipográfiai nyitányok, utána strukturált „proof panels”; a diagramok és képernyőképek nem dekorációk, hanem címkézett állítást támasztanak alá.
- **Interakció:** visszafogott, `prefers-reduced-motion` mellett kikapcsolható/egyszerűsített átmenetek. A 3D, videó és glow csak progresszív díszítés; sosem blokkolja a fő üzenetet vagy LCP-t.

A nyilvános oldal 1024 px-től desktop-first kompozíciót ad. Keskeny képernyőkön reszponzív, olvasható dokumentumélményre omlik össze; külön mobil-natív alkalmazás vagy operátori mobil UX nem része ennek a tervnek.

## 5. Megvalósítási architektúra

- **Első implementáció:** React + Vite + TypeScript, a meglévő FastAPI helyi szolgáltatás által statikusan kiszolgálva. Ez a Control Room meglévő build- és üzemeltetési mintáját követi, így a publikus Home önálló alkalmazás marad, miközben nem nyit új szerver- vagy auth-surface-et.
- **Következő architekturális döntési pont:** Next.js App Router csak akkor indokolt, amikor az MDX Build Log, a többoldalas SEO/metadata-generálás vagy a szerveroldali contact-űrlap ténylegesen belép a scope-ba. Addig statikus, mérhető React-oldalak készülnek; a Vite alkalmazást nem szabad félkész Next-migrációval keverni.
- **Tartalom:** MDX a Build Loghoz és a kurált docs-oldalakhoz. A frontmatter tartalmazza az állapotot, dátumot, bizonyítékszintet, korlátokat, következő lépést, SEO-metaadatokat és kapcsolódó linkeket.
- **Komponensek:** `StatusBadge`, `EvidenceCard`, `BuildLogCard`, `ArchitectureFlow`, `EmbodimentCard`, `SafetyBoundary`, `Metric`, `Callout`, `PrimaryCta` és `ContactForm`. A komponensek immutábilis bemeneti adatokból renderelnek.
- **Asset pipeline:** optimalizált képek (`next/image`), modern formátumok (AVIF/WebP), több méret, videóposzter, lazy-load a hajtás alatt. Dekoratív 3D csak kliensoldali, dinamikus és előre meghatározott költségkerettel töltődik.
- **Űrlapkezelés:** szerveroldali validáció, botvédelem és rate limit; a minimális szükséges adatokat kérje, a privacy link kötelezően közvetlenül az elküldés előtt jelenjen meg.
- **Analitika:** privacy-tudatos, eseményalapú mérés. Minimális események: CTA-kattintás, docs-átkattintás, Build Log olvasás, űrlap-megnyitás, sikeres érdeklődés. A consent és jogi követelmények előtt ne aktiválódjon opcionális követés.

## 6. SEO, teljesítmény és akadálymentesség

### SEO

- Célkulcsszavak: `autonomous robotics platform`, `cognitive robotics`, `embodied AI platform`, `ROS 2 PX4 digital twin`, `governed autonomy`, `robotics mission orchestration`, `AI safety for robots`.
- Minden indexelhető oldal egyedi `title`, meta description, kanonikus URL, Open Graph/Twitter metaadat és egyetlen H1 elemet kap.
- Strukturált adatok: `Organization` sitewide; `SoftwareApplication` a platformoldalon; `Article` a Build Log-bejegyzéseken. Csak igazolható, publikus állítások kerülhetnek a JSON-LD-be.
- Generált `sitemap.xml`, `robots.txt`, RSS vagy Atom feed a Build Loghoz. A staging és preview környezet `noindex`.
- Belső linkelés: a Home → Platform → Technology/Embodiments/Developers/Build Log útvonalat, valamint minden állítás → bizonyíték/korlát linket támogatni kell.

### Performance

- Teljesítménybudget első slice-ra: LCP ≤ 2,5 s, INP ≤ 200 ms, CLS ≤ 0,1 p75 mobil és desktop valós felhasználói adaton; lab környezetben Lighthouse Performance ≥ 90 a homepage-en.
- A hero nem függhet autoplay videótól vagy WebGL-től. Elsőként szöveg és optimalizált, méretezett hero-kép jelenik meg.
- Kritikus fontok subsetelve és `font-display: swap`-pal; nem kritikus script `defer`/lazy-load; harmadik fél scriptek csak indokoltan.
- Képekhez szélesség/magasság vagy stabil `aspect-ratio`; videókhoz poster; a Build Log médiája a hajtás alatt késleltetetten töltődik.
- CI-ben Lighthouse CI vagy ekvivalens audit fut a Home és a Build Log oldalra, és a budget túllépése blokkolja a merge-et.

### Akadálymentesség

- Cél: WCAG 2.2 AA. Szemantikus landmarkok, logikus címsorstruktúra, látható fókusz, skip link, teljes billentyűzetes navigáció.
- Szövegkontraszt legalább 4,5:1; a cyan/amber/red állapotokhoz mindig szöveg és ikon is társul.
- Minden információt hordozó képhez értelmes alt; dekoratív kép üres alt; diagramokhoz rövid szöveges összefoglaló vagy táblázatos alternatíva.
- A mozgó assetek megállíthatók, és tiszteletben tartják a `prefers-reduced-motion` beállítást. Videókhoz felirat és transcript, ha beszédet vagy érdemi narratívát tartalmaznak.
- Az űrlapmezőknek programozott címkéjük, hibáiknak egyértelmű szöveges visszajelzésük és hozzáférhető státuszjelzésük van.

## 7. Fázisolt roadmap

| Fázis | Eredmény | Függőségek | Kilépési feltétel |
| --- | --- | --- | --- |
| 0. Alapozás | Next.js alap, design tokenek, MDX séma, routing, SEO keret, analytics/consent döntés | brand assets, domain és privacy szöveg | preview környezet, alap oldalshell, CI audit |
| 1. Első publikus slice | Home, Platform, X500, Build Log index és egy bejegyzés, Contact, Privacy/Legal; bizonyítékblokkok és CTA-k | jóváhagyott X500 asset és igazolható build evidence | az alábbi acceptance criteria teljesül |
| 2. Technikai mélyítés | Cognitive Runtime, Safety & Governance, Digital Twin, Developers és docs index | technikai leírások, SDK/API állapotok | minden technológiai állítás állapot- és korlátcímkézett |
| 3. Embodiment narratíva | Embodiments landing, Rover roadmap, Humanoid vision; Build Log feed/RSS | roadmap ownership | roadmap-elemek nem jelennek meg kész termékként |
| 4. Optimalizálás és governance | SEO-content program, A/B tesztek csak consenttel, performance/RUM, rendszeres evidence review | mérési baseline, tartalomfelelős | CWV és accessibility budget tartósan teljesül |

### Első slice — konkrét elfogadási feltételek

1. A `/` oldalon az első viewportban szerepel a ByteWolf neve, a „One Brain. Many Bodies.” üzenet, a platformkategória, egy **Explore the platform** CTA és egy X500-hoz kötött, **Aktív** állapotjelzés.
2. A `/` oldal tartalmazza a hétlépéses platformfolyamot, amelyben a `Safety Validation` és `Human Approval` elkülönül a `Body Execution` lépéstől; mellette vagy alatta világosan olvasható, hogy az AI nem vezérel közvetlenül aktuátorokat.
3. A `/platform` és `/embodiments/x500-drone` oldalak közzétehetők úgy, hogy minden lényeges állítás mellett vagy a tartalmi blokkban egyértelműen megjelenik az állapot (`Aktív`/`Kísérleti`/`Tervezett`/`Vízió`) és a kapcsolódó evidence vagy korlát hivatkozása.
4. A `/build-log` index legalább egy valódi, dátumozott bejegyzést listáz; a bejegyzés tartalmaz milestone-t, konkrét bizonyítékot, korlátokat és következő lépést. Nem állíthat általános hardveres vagy repülési teljesítményt az evidence szintjén túl.
5. A `/contact` űrlap kliens- és szerveroldalon validálja a kötelező mezőket, hibáit hozzáférhetően jelzi, spam/rate-limit védelemmel rendelkezik, és a privacy oldalra mutat.
6. Minden first-slice útvonal rendelkezik egyedi címmel, meta descriptionnel, kanonikus URL-lel, Open Graph metaadattal és helyes `robots` viselkedéssel; `sitemap.xml` és `robots.txt` generálódik.
7. A homepage és a Build Log index teljesíti a Lighthouse Accessibility ≥ 95 és Performance ≥ 90 eredményt az elfogadott preview-konfigurációban, valamint nem mutat blokkoló billentyűzetes vagy fókuszhibát manuális smoke teszten.
8. A hero tartalom JavaScript, autoplay videó és 3D betöltése nélkül is olvasható és használható; `prefers-reduced-motion` mellett nincs folyamatos nem lényegi animáció.
9. A staging/preview deployment nem indexelhető, a production domainhez pedig nincs `noindex`; az alap `Organization` strukturált adat érvényes.
10. A publikus weboldal nem tartalmaz bejelentkezést, robot- vagy küldetésindító vezérlést, telemetria pollingot, operátori térképet, élő videófolyamot vagy valós idejű kontrollcsatornát.

## 8. Tartalom-governance

- **Tulajdonosok:** marketing/design felel a narratíváért; engineering felel a technikai pontosságért és evidence-hivatkozásokért; jogi felelős hagyja jóvá privacy/legal változásokat.
- **Publikálási kapu:** új technikai állítás vagy Build Log bejegyzés előtt engineering review kötelező. A „működik” állításhoz konkrét bizonyíték és pontos proof level tartozik.
- **Frissítési ritmus:** Build Log milestone-onként, vagy legalább havonta; roadmap-állapotok negyedéves felülvizsgálata.
- **Visszavonás:** ha egy bizonyíték, build vagy állítás érvényét veszti, a bejegyzés nem törlődik csendben; helyette dátumozott korrekciót és aktuális státuszt kap.

## 9. Explicit out of scope

Ez a terv és az első implementáció **nem** tartalmazza:

- mobil-natív iOS/Android alkalmazást, mobil operátori navigációt, push-jóváhagyást vagy eszközpárosítást;
- autentikációt, felhasználói fiókokat, szerepköröket, ügyfélportált vagy hozzáférés-kezelést;
- robot-, flotta- vagy küldetésvezérlést, SafetyGate futtatást, emberi jóváhagyási folyamatot, telemetriát, live camera streamet, térképes tervezőt, emergency kontrollt vagy operációs vezérlőfelületet;
- a roadmapen szereplő Rover vagy Humanoid képességek kész termékként való értékesítését;
- nem bizonyított autonóm, biztonsági vagy hardveres teljesítményállításokat.

A későbbi, autentikált Control Room és a mobil alkalmazás önálló termékfelületként, külön biztonsági, UX- és compliance specifikáció alapján készülhet; nem a marketing site kiterjesztéseként.
