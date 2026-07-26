import { FormEvent, MouseEvent, useEffect, useRef, useState } from "react";

import { api, post } from "./api";

type Envelope = {
  max_altitude_m: number;
  max_radius_m: number;
  minimum_battery_percent_to_start: number;
  geofence_vertices_m: Array<{ north_m: number; east_m: number }>;
};

type Mission = {
  plan_id: string;
  summary: string;
  goal: string;
  steps: string[];
  waypoints?: Array<{ north_m: number; east_m: number; altitude_m?: number }>;
};

type GatewayReply = { text: string; plan_id: string | null };
type OccupancyCell = { north_m: number; east_m: number; cell_size_m: number; disputed?: boolean };
type OccupancyDocument = { occupancy_only?: unknown; cells?: unknown };
type MapBackground = { metres_per_pixel: number; width: number; height: number; centre_north_m: number; centre_east_m: number; captured_at?: string };

function isOccupancyCell(value: unknown): value is OccupancyCell {
  if (!value || typeof value !== "object") return false;
  const cell = value as Record<string, unknown>;
  return [cell.north_m, cell.east_m, cell.cell_size_m].every((coordinate) => typeof coordinate === "number" && Number.isFinite(coordinate))
    && (cell.cell_size_m as number) > 0
    && (cell.disputed === undefined || typeof cell.disputed === "boolean");
}

export function MissionPage() {
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [north, setNorth] = useState("5");
  const [east, setEast] = useState("0");
  const [altitude, setAltitude] = useState("2");
  const [kind, setKind] = useState<"point" | "survey">("point");
  const [radius, setRadius] = useState("5");
  const [spacing, setSpacing] = useState("3");
  const [goal, setGoal] = useState("Biztonságos pontküldetés a szimulációban");
  const [plan, setPlan] = useState<Mission | null>(null);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("A biztonsági korlátok betöltése…");
  const [occupancyCells, setOccupancyCells] = useState<OccupancyCell[]>([]);
  const [mapBackground, setMapBackground] = useState<MapBackground | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const statusTimer = useRef<number | null>(null);
  const mapSize = 400;
  const mapRadius = envelope?.max_radius_m ?? 50;
  const mapScale = (mapSize / 2) / mapRadius;
  const proposalState = plan ? "ellenőrizve" : busy ? "ellenőrzés alatt" : "előkészítés alatt";

  function logEvent(text: string) {
    const timestamp = new Date().toLocaleTimeString("hu-HU");
    setEvents((current) => [`${timestamp} · ${text}`, ...current].slice(0, 20));
  }

  function pickOnMap(event: MouseEvent<SVGSVGElement>) {
    if (!envelope) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const east_m = ((event.clientX - box.left) / box.width * mapSize - mapSize / 2) / mapScale;
    const north_m = (mapSize / 2 - (event.clientY - box.top) / box.height * mapSize) / mapScale;
    setNorth(north_m.toFixed(1));
    setEast(east_m.toFixed(1));
    setPlan(null);
    setStatus(`Térképi cél kijelölve: É ${north_m.toFixed(1)} m, K ${east_m.toFixed(1)} m. A SafetyGate ellenőrzi a tervet.`);
  }

  useEffect(() => {
    void api<Envelope>("/api/v1/safety-envelope")
      .then((value) => { setEnvelope(value); setStatus("Jelölj ki egy célpontot és kérj tervet."); })
      .catch((error: Error) => setStatus(`A safety-profil nem olvasható: ${error.message}`));
  }, []);

  useEffect(() => {
    let active = true;
    async function refreshBasemap() {
      try {
        const value = await api<MapBackground>("/api/v1/map-view/meta");
        const valid = [value.metres_per_pixel, value.width, value.height, value.centre_north_m, value.centre_east_m].every(Number.isFinite)
          && value.metres_per_pixel > 0 && value.width > 0 && value.height > 0;
        if (active) setMapBackground(valid ? value : null);
      } catch {
        if (active) setMapBackground(null);
      }
    }
    void refreshBasemap();
    const interval = window.setInterval(() => void refreshBasemap(), 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  useEffect(() => {
    let active = true;
    async function refreshWorldMap() {
      try {
        const value = await api<OccupancyDocument>("/api/v1/world-map");
        const verifiedCells = value.occupancy_only === true && Array.isArray(value.cells)
          ? value.cells.filter(isOccupancyCell)
          : [];
        if (active) setOccupancyCells(verifiedCells);
      } catch {
        if (active) setOccupancyCells([]);
      }
    }
    void refreshWorldMap();
    const interval = window.setInterval(() => void refreshWorldMap(), 5000);
    return () => { active = false; window.clearInterval(interval); };
  }, []);

  useEffect(() => () => {
    if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
  }, []);

  async function review(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const north_m = Number(north);
    const east_m = Number(east);
    const altitude_m = Number(altitude);
    const radius_m = Number(radius);
    const spacing_m = Number(spacing);
    if (![north_m, east_m, altitude_m].every(Number.isFinite) || !goal.trim() || (kind === "survey" && (![radius_m, spacing_m].every(Number.isFinite) || radius_m < 2 || spacing_m < 1 || spacing_m > 15))) {
      setStatus("Adj meg véges koordinátát, magasságot és célt.");
      return;
    }
    setBusy(true);
    setPlan(null);
    setStatus("SafetyGate ellenőrzés fut…");
    try {
      const nextPlan = await post<Mission>(
        kind === "survey" ? "/api/v1/missions/survey" : "/api/v1/missions/point",
        kind === "survey"
          ? { centre_north_m: north_m, centre_east_m: east_m, radius_m, spacing_m, altitude_m, goal: goal.trim() }
          : { north_m, east_m, altitude_m, goal: goal.trim() },
      );
      setPlan(nextPlan);
      setStatus("A terv ellenőrzött; a küldetés még nem indult el.");
      logEvent(`Terv jóváhagyásra vár: ${nextPlan.summary}`);
    } catch (error) {
      setStatus(`A SafetyGate elutasította: ${error instanceof Error ? error.message : "ismeretlen hiba"}`);
      logEvent("A SafetyGate elutasította a tervet.");
    } finally { setBusy(false); }
  }

  async function decide(action: "approve" | "cancel") {
    if (!plan || busy) return;
    setBusy(true);
    try {
      const reply = await post<GatewayReply>(`/api/v1/plans/${action}`, { plan_id: plan.plan_id });
      setStatus(reply.text);
      setPlan(null);
      logEvent(action === "approve" ? `Küldetés jóváhagyva: ${reply.plan_id ?? "ismeretlen terv"}` : "A jóváhagyásra váró terv visszavonva.");
      if (action === "approve" && reply.plan_id) monitor(reply.plan_id);
    } catch (error) {
      setStatus(`Hiba: ${error instanceof Error ? error.message : "ismeretlen hiba"}`);
    } finally { setBusy(false); }
  }

  function monitor(planId: string) {
    if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
    statusTimer.current = window.setInterval(() => {
      void api<{ status: string; message: string }>(`/api/v1/plans/${encodeURIComponent(planId)}/status`)
        .then((result) => {
          if (result.status !== "completed" && result.status !== "failed") return;
          if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
          statusTimer.current = null;
          setStatus(result.message);
          logEvent(result.message);
        })
        .catch(() => {
          if (statusTimer.current !== null) window.clearInterval(statusTimer.current);
          statusTimer.current = null;
          setStatus("A végrehajtási állapot nem ellenőrizhető. A naplót vagy a Visszajátszás nézetet ellenőrizd.");
          logEvent("A végrehajtási állapot figyelése megszakadt.");
        });
    }, 1000);
  }

  return (
    <section className="content-card" aria-labelledby="mission-title">
      <div className="section-heading"><div><p className="eyebrow">SAFETYGATE-ELLENŐRZÖTT</p><h2 id="mission-title">Küldetéstervezés</h2></div></div>
      <section className="mission-events" aria-labelledby="mission-lifecycle-title">
        <p className="eyebrow">KÜLDETÉSI ÉLETCIKLUS · {proposalState.toUpperCase()}</p>
        <h3 id="mission-lifecycle-title">Javaslatból csak kifejezett jóváhagyással lesz indítás</h3>
        <ol>
          <li>1. Javaslat elküldve</li>
          <li>2. SafetyGate ellenőrizte</li>
          <li>3. Operátori jóváhagyás szükséges</li>
        </ol>
        <p className="muted">A térképi kijelölés és a terv-ellenőrzés nem ad ki repülési parancsot. Csak az ellenőrzött tervhez tartozó, külön jóváhagyás kezdeményezhet indítást.</p>
      </section>
      {envelope && <p className="envelope">Aktív korlát: max. {envelope.max_altitude_m} m magasság · {envelope.max_radius_m} m sugár · indításhoz min. {envelope.minimum_battery_percent_to_start}% akkumulátor.</p>}
      <form className="mission-form" onSubmit={review}>
        <fieldset className="mission-kind"><legend>Küldetéstípus</legend><label><input type="radio" name="mission-kind" checked={kind === "point"} onChange={() => setKind("point")} /> Pontküldetés</label><label><input type="radio" name="mission-kind" checked={kind === "survey"} onChange={() => setKind("survey")} /> Terület felderítése</label></fieldset>
        <div className="mission-map-panel"><p className="eyebrow">{envelope ? "KATTINTS A CÉLPONTRA" : "TÉRKÉPI SKÁLA BETÖLTÉSE"}</p><svg className="mission-map" viewBox={`0 0 ${mapSize} ${mapSize}`} role="img" aria-label="Küldetési térkép" aria-disabled={!envelope} onClick={pickOnMap}>
          {mapBackground && <image href={`/api/v1/map-view?v=${encodeURIComponent(mapBackground.captured_at ?? "")}`} x={mapSize / 2 + mapBackground.centre_east_m * mapScale - mapBackground.width * mapBackground.metres_per_pixel * mapScale / 2} y={mapSize / 2 - mapBackground.centre_north_m * mapScale - mapBackground.height * mapBackground.metres_per_pixel * mapScale / 2} width={mapBackground.width * mapBackground.metres_per_pixel * mapScale} height={mapBackground.height * mapBackground.metres_per_pixel * mapScale} opacity="0.72" preserveAspectRatio="none" />}
          <line x1={mapSize / 2} y1="0" x2={mapSize / 2} y2={mapSize} className="map-axis" /><line x1="0" y1={mapSize / 2} x2={mapSize} y2={mapSize / 2} className="map-axis" />
          <circle cx={mapSize / 2} cy={mapSize / 2} r={mapRadius * mapScale} className="map-radius" />
          {envelope && (envelope.geofence_vertices_m ?? []).length > 2 && <polygon className="map-fence" points={(envelope.geofence_vertices_m ?? []).map((vertex) => `${mapSize / 2 + vertex.east_m * mapScale},${mapSize / 2 - vertex.north_m * mapScale}`).join(" ")} />}
          {occupancyCells.map((cell, index) => {
            const side = Math.max(3, cell.cell_size_m * mapScale);
            return <rect key={`${cell.north_m}-${cell.east_m}-${index}`} className={cell.disputed ? "map-obstacle map-obstacle--disputed" : "map-obstacle"} aria-label={`${cell.disputed ? "Vitatott" : "Mért"} akadálybizonyíték: É ${cell.north_m} m, K ${cell.east_m} m`} x={mapSize / 2 + cell.east_m * mapScale - side / 2} y={mapSize / 2 - cell.north_m * mapScale - side / 2} width={side} height={side} rx="2" />;
          })}
          <circle cx={mapSize / 2} cy={mapSize / 2} r="4" className="map-home" />
          {plan?.waypoints && <polyline className="map-route" points={[`${mapSize / 2},${mapSize / 2}`, ...plan.waypoints.map((waypoint) => `${mapSize / 2 + waypoint.east_m * mapScale},${mapSize / 2 - waypoint.north_m * mapScale}`)].join(" ")} />}
          {kind === "survey" && Number(radius) > 0 && Number.isFinite(Number(north)) && Number.isFinite(Number(east)) && <circle className="map-survey-area" cx={mapSize / 2 + Number(east) * mapScale} cy={mapSize / 2 - Number(north) * mapScale} r={Number(radius) * mapScale} />}
          {Number.isFinite(Number(north)) && Number.isFinite(Number(east)) && <circle className="map-target" cx={mapSize / 2 + Number(east) * mapScale} cy={mapSize / 2 - Number(north) * mapScale} r="6" />}
          <text x={mapSize / 2 + 8} y="18" className="map-label">É</text><text x="8" y={mapSize - 10} className="map-label">kiindulópont</text>
        </svg><p className="muted">A kör a szerver által szolgáltatott sugárkorlát; a sárga szaggatott alakzat a geofence; a rózsaszín cellák mért akadálybizonyítékok. A kattintás csak tervez, nem repül; billentyűzettel az alábbi koordinátamezők használhatók.</p></div>
        <label>Észak (m)<input type="number" value={north} onChange={(event) => setNorth(event.target.value)} /></label>
        <label>Kelet (m)<input type="number" value={east} onChange={(event) => setEast(event.target.value)} /></label>
        <label>Magasság (m)<input type="number" min="0" step="0.1" value={altitude} onChange={(event) => setAltitude(event.target.value)} /></label>
        {kind === "survey" && <><label>Sugár (m)<input aria-label="Sugár (m)" type="number" min="2" value={radius} onChange={(event) => setRadius(event.target.value)} /></label><label>Vonal-köz (m)<input type="number" min="1" max="15" value={spacing} onChange={(event) => setSpacing(event.target.value)} /></label></>}
        <label className="wide">Cél<input value={goal} maxLength={240} onChange={(event) => setGoal(event.target.value)} /></label>
        <button className="primary" disabled={busy || !envelope}>{busy ? "Ellenőrzés…" : "Terv ellenőrzése"}</button>
      </form>
      {plan && <div className="approval-panel" aria-labelledby="approval-title"><p className="eyebrow">SAFETYGATE-BIZONYÍTÉK · JÓVÁHAGYÁSRA VÁR</p><strong id="approval-title">Ellenőrzött terv: {plan.summary}</strong><p>{plan.goal}</p>{plan.waypoints && <p className="muted">Útvonal-bizonyíték: {plan.waypoints.length} SafetyGate által összeállított waypoint.</p>}<h3>Tervezett lépések</h3><ol>{plan.steps.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}</ol><p className="muted">Ez a terv még nem indult el. A jóváhagyás ehhez a tervazonosítóhoz kötött, és visszavonható az indítás előtt.</p><button className="primary" type="button" onClick={() => void decide("approve")} disabled={busy}>Kifejezett jóváhagyás és indítás</button><button type="button" onClick={() => void decide("cancel")} disabled={busy}>Terv visszavonása</button></div>}
      <p className="muted" role="status">{status}</p>
      <section className="mission-events" aria-labelledby="mission-events-title"><p className="eyebrow">HELYI OPERÁTORI NAPLÓ</p><h3 id="mission-events-title">Küldetési események</h3>{events.length === 0 ? <p className="muted">Még nincs esemény ebben a böngésző-sessionben.</p> : <ol>{events.map((event, index) => <li key={`${event}-${index}`}>{event}</li>)}</ol>}</section>
    </section>
  );
}
