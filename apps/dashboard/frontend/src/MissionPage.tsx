import { FormEvent, MouseEvent, useEffect, useRef, useState } from "react";

import { ControlRoomMapPanel, type ControlRoomMapWaypoint } from "./ControlRoomMapPanel";
import { api, post } from "./api";
import { pointFromLocalOffset } from "./localMissionCoordinates";
import { mapHomeAnchor, mapTelemetryConnection, parseMapTelemetry, type MapHomePosition } from "./controlRoomMapTelemetry";
import {
  buildDemoWaypointMissionPayload,
  MAX_MAP_WAYPOINTS,
  type MapMissionWaypoint,
} from "./missionWaypointPlan";

// sessionStorage, like the operator session id in api.ts: per tab, survives reload.
const ACTIVE_PLAN_KEY = "bytewolfActivePlan";

function readActivePlan(): string | null {
  try {
    return window.sessionStorage.getItem(ACTIVE_PLAN_KEY);
  } catch {
    return null;
  }
}

function rememberActivePlan(planId: string | null) {
  try {
    if (planId === null) window.sessionStorage.removeItem(ACTIVE_PLAN_KEY);
    else window.sessionStorage.setItem(ACTIVE_PLAN_KEY, planId);
  } catch {
    // Storage blocked: monitoring still works until the next reload.
  }
}

type Envelope = {
  max_altitude_m: number;
  max_radius_m: number;
  minimum_battery_percent_to_start: number;
  geofence_vertices_m: Array<{ north_m: number; east_m: number }>;
};

type Mission = {
  plan_id: string;
  plan_hash?: string | null;
  summary: string;
  goal: string;
  steps: string[];
  waypoints?: Array<{ north_m: number; east_m: number; altitude_m?: number }>;
};

type GatewayReply = { text: string; plan_id: string | null };
type ApprovalTokenReply = { approval_token: string; plan_hash: string; expires_in_s: number };
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

//: The view distances offered, in metres of radius. 500 m shows the whole
//: Baylands world (about 924 m across) rather than the 2 km safety radius,
//: which is an envelope limit and not a place the simulated world extends to.
const MAP_RANGES_M = [50, 200, 500] as const;

type MissionPageProps = {
  commandsEnabled: boolean;
};

export function MissionPage({ commandsEnabled }: MissionPageProps) {
  const [envelope, setEnvelope] = useState<Envelope | null>(null);
  const [north, setNorth] = useState("5");
  const [east, setEast] = useState("0");
  const [altitude, setAltitude] = useState("2");
  const [kind, setKind] = useState<"point" | "survey" | "waypoints">("point");
  const [radius, setRadius] = useState("5");
  const [spacing, setSpacing] = useState("3");
  const [goal, setGoal] = useState("Biztonságos pontküldetés a szimulációban");
  const [plan, setPlan] = useState<Mission | null>(null);
  const [approvalToken, setApprovalToken] = useState<string | null>(null);
  const [confirmStep, setConfirmStep] = useState<0 | 1>(0);
  const [busy, setBusy] = useState(false);
  const [status, setStatus] = useState("A biztonsági korlátok betöltése…");
  const [occupancyCells, setOccupancyCells] = useState<OccupancyCell[]>([]);
  const [mapBackground, setMapBackground] = useState<MapBackground | null>(null);
  const [events, setEvents] = useState<string[]>([]);
  const [pendingTarget, setPendingTarget] = useState<{ latitude_deg: number; longitude_deg: number } | null>(null);
  const [mapWaypoints, setMapWaypoints] = useState<ControlRoomMapWaypoint[]>([]);
  const [mapHome, setMapHome] = useState<MapHomePosition | null>(null);
  const statusMonitor = useRef<AbortController | null>(null);
  const mapSize = 400;
  // The view is chosen, not derived from the safety radius. Deriving it meant
  // the envelope's widening to 2 km silently made this map useless: one screen
  // pixel became ten metres, the aerial basemap shrank to 18 px, and a 2 m
  // obstacle cell to a fifth of a pixel. The radius is still drawn -- it is
  // just no longer what decides how much ground fits on screen.
  // Typed as the whole union, not as whatever the first entry happens to be.
  // `MAP_RANGES_M` is `as const`, so inferring from `[0]` narrows the state to
  // the literal 50 and makes selecting 200 or 500 a compile error - which is
  // what broke `tsc -b` while `npm test` stayed green, because vitest does not
  // typecheck and CI never builds the frontend.
  const [mapRange, setMapRange] = useState<(typeof MAP_RANGES_M)[number]>(MAP_RANGES_M[0]);
  const mapScale = (mapSize / 2) / mapRange;
  const envelopeRadius = envelope?.max_radius_m ?? null;
  const proposalState = plan ? "ellenőrizve" : busy ? "ellenőrzés alatt" : "előkészítés alatt";

  function logEvent(text: string) {
    const timestamp = new Date().toLocaleTimeString("hu-HU");
    setEvents((current) => [`${timestamp} · ${text}`, ...current].slice(0, 20));
  }

  function applyWaypointSelection(waypoint: ControlRoomMapWaypoint) {
    setNorth(waypoint.north_m.toFixed(1));
    setEast(waypoint.east_m.toFixed(1));
    setPendingTarget({ latitude_deg: waypoint.latitude_deg, longitude_deg: waypoint.longitude_deg });
    setPlan(null);
    setStatus(`Térképi cél kijelölve: É ${waypoint.north_m.toFixed(1)} m, K ${waypoint.east_m.toFixed(1)} m. A SafetyGate ellenőrzi a tervet.`);
  }

  function pickOnMap(event: MouseEvent<SVGSVGElement>) {
    if (!envelope) return;
    const box = event.currentTarget.getBoundingClientRect();
    if (!box.width || !box.height) return;
    const east_m = ((event.clientX - box.left) / box.width * mapSize - mapSize / 2) / mapScale;
    const north_m = (mapSize / 2 - (event.clientY - box.top) / box.height * mapSize) / mapScale;
    const point = mapHome === null ? null : pointFromLocalOffset(mapHome, { north_m, east_m });
    if (kind === "waypoints") {
      if (point === null) {
        setStatus("PX4 HOME_POSITION hiányzik; waypoint nem vehető fel.");
        return;
      }
      appendMapWaypoint({ north_m, east_m, ...point });
      return;
    }
    if (point === null) {
      // No home yet: keep the N/E pick, but never invent a 0°,0° map marker.
      setNorth(north_m.toFixed(1));
      setEast(east_m.toFixed(1));
      setPendingTarget(null);
      setPlan(null);
      setStatus(`Térképi cél kijelölve: É ${north_m.toFixed(1)} m, K ${east_m.toFixed(1)} m. A SafetyGate ellenőrzi a tervet.`);
      return;
    }
    applyWaypointSelection({ north_m, east_m, ...point });
  }

  function clearPendingTarget() {
    setPendingTarget(null);
    setPlan(null);
    setStatus("A függő térképi cél törölve.");
  }

  function appendMapWaypoint(waypoint: ControlRoomMapWaypoint) {
    if (mapWaypoints.length >= MAX_MAP_WAYPOINTS) return;
    setMapWaypoints((current) => {
      const next = [...current, waypoint];
      setStatus(`Waypoint ${next.length} hozzáadva: É ${waypoint.north_m.toFixed(1)} m, K ${waypoint.east_m.toFixed(1)} m.`);
      return next;
    });
    setNorth(waypoint.north_m.toFixed(1));
    setEast(waypoint.east_m.toFixed(1));
    setPlan(null);
  }

  function removeLastMapWaypoint() {
    setMapWaypoints((current) => {
      const next = current.slice(0, -1);
      const last = next.at(-1);
      if (last) {
        setNorth(last.north_m.toFixed(1));
        setEast(last.east_m.toFixed(1));
      }
      return next;
    });
    setPlan(null);
    setStatus("Az utolsó waypoint eltávolítva a listából.");
  }

  function clearMapWaypoints() {
    setMapWaypoints([]);
    setPlan(null);
    setStatus("A waypoint lista törölve.");
  }

  function removeMapWaypointAt(index: number) {
    setMapWaypoints((current) => current.filter((_, waypointIndex) => waypointIndex !== index));
    setPlan(null);
    setStatus(`Waypoint ${index + 1} törölve.`);
  }

  useEffect(() => {
    void api<Envelope>("/api/v1/safety-envelope")
      .then((value) => { setEnvelope(value); if (!statusMonitor.current) setStatus("Jelölj ki egy célpontot és kérj tervet."); })
      .catch((error: Error) => setStatus(`A safety-profil nem olvasható: ${error.message}`));
  }, []);

  useEffect(() => {
    // The mission runs server-side and survives a reload; only this page's polling did not.
    const planId = readActivePlan();
    if (planId !== null) {
      setStatus(`Futó küldetés követése folytatódik: ${planId}`);
      logEvent(`Újratöltés után a küldetés követése folytatódik: ${planId}`);
      monitor(planId);
    }
    return () => {
      statusMonitor.current?.abort();
      statusMonitor.current = null;
    };
  }, []);

  useEffect(() => {
    let active = true;
    async function refreshHome() {
      try {
        const payload = await api<unknown>("/api/v1/telemetry");
        if (!active) return;
        const sample = parseMapTelemetry(payload);
        setMapHome(sample.position !== null && mapTelemetryConnection(sample).state === "ready"
          ? mapHomeAnchor(sample)
          : null);
      } catch {
        if (active) setMapHome(null);
      }
    }
    void refreshHome();
    const timer = window.setInterval(() => void refreshHome(), 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);

  useEffect(() => {
    if (kind === "waypoints" || mapHome === null) return;
    const north_m = Number(north);
    const east_m = Number(east);
    if (!Number.isFinite(north_m) || !Number.isFinite(east_m)) return;
    const pending = pointFromLocalOffset(mapHome, { north_m, east_m });
    setPendingTarget({ latitude_deg: pending.latitude_deg, longitude_deg: pending.longitude_deg });
  }, [east, kind, mapHome, north]);

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

  async function review(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    const north_m = Number(north);
    const east_m = Number(east);
    const altitude_m = Number(altitude);
    const radius_m = Number(radius);
    const spacing_m = Number(spacing);
    if (kind === "waypoints") {
      if (!mapHome) {
        setStatus("PX4 HOME_POSITION hiányzik; a többpontos terv nem küldhető.");
        return;
      }
      if (!goal.trim() || mapWaypoints.length === 0 || !Number.isFinite(altitude_m) || altitude_m <= 0) {
        setStatus("A többpontos tervhez legalább egy waypoint, véges magasság és cél szükséges.");
        return;
      }
    } else if (![north_m, east_m, altitude_m].every(Number.isFinite) || !goal.trim() || (kind === "survey" && (![radius_m, spacing_m].every(Number.isFinite) || radius_m < 2 || spacing_m < 1 || spacing_m > 15))) {
      setStatus("Adj meg véges koordinátát, magasságot és célt.");
      return;
    }
    setBusy(true);
    setPlan(null);
    setStatus("SafetyGate ellenőrzés fut…");
    try {
      const waypointPayload = mapWaypoints.map<MapMissionWaypoint>((waypoint) => ({
        latitude_deg: waypoint.latitude_deg,
        longitude_deg: waypoint.longitude_deg,
        relative_altitude_m: altitude_m,
      }));
      const homePayload = {
        latitude_deg: mapHome!.latitude_deg,
        longitude_deg: mapHome!.longitude_deg,
        absolute_altitude_m: mapHome!.absolute_altitude_m,
        captured_at: mapHome!.captured_at,
      };
      const nextPlan = await post<Mission>(
        kind === "survey"
          ? "/api/v1/missions/survey"
          : kind === "waypoints"
            ? "/api/v1/missions/demo-waypoint-mission"
            : "/api/v1/missions/point",
        kind === "survey"
          ? { centre_north_m: north_m, centre_east_m: east_m, radius_m, spacing_m, altitude_m, goal: goal.trim() }
          : kind === "waypoints"
            ? buildDemoWaypointMissionPayload(waypointPayload, altitude_m, goal, homePayload)
            : { north_m, east_m, altitude_m, goal: goal.trim() },
      );
      setPlan(nextPlan);
      setApprovalToken(null);
      setConfirmStep(0);
      setStatus("A terv ellenőrzött; a küldetés még nem indult el.");
      logEvent(`Terv jóváhagyásra vár: ${nextPlan.summary}`);
    } catch (error) {
      setStatus(`A SafetyGate elutasította: ${error instanceof Error ? error.message : "ismeretlen hiba"}`);
      logEvent("A SafetyGate elutasította a tervet.");
    } finally { setBusy(false); }
  }

  async function prepareConfirmation() {
    if (!plan || busy) return;
    setBusy(true);
    try {
      const payload = await post<ApprovalTokenReply>("/api/v1/plans/approval-token", { plan_id: plan.plan_id });
      if (plan.plan_hash && payload.plan_hash !== plan.plan_hash) {
        throw new Error("A terv megváltozott a javaslat óta; ellenőrizd újra.");
      }
      if (!plan.plan_hash) {
        setPlan({ ...plan, plan_hash: payload.plan_hash });
      }
      setApprovalToken(payload.approval_token);
      setConfirmStep(1);
      setStatus("Ellenőrizd a terv-hash értéket, majd erősítsd meg a jóváhagyást.");
    } catch (error) {
      setConfirmStep(0);
      setApprovalToken(null);
      setStatus(`A jóváhagyási token nem kérhető: ${error instanceof Error ? error.message : "ismeretlen hiba"}`);
    } finally {
      setBusy(false);
    }
  }

  async function decide(action: "approve" | "cancel") {
    if (!plan || busy) return;
    if (action === "approve" && confirmStep === 0) {
      await prepareConfirmation();
      return;
    }
    setBusy(true);
    try {
      const reply = await post<GatewayReply>(
        `/api/v1/plans/${action}`,
        action === "approve"
          ? { plan_id: plan.plan_id, approval_token: approvalToken ?? "" }
          : { plan_id: plan.plan_id },
      );
      setStatus(reply.text);
      setPlan(null);
      setApprovalToken(null);
      setConfirmStep(0);
      logEvent(action === "approve" ? `Küldetés jóváhagyva: ${reply.plan_id ?? "ismeretlen terv"}` : "A jóváhagyásra váró terv visszavonva.");
      if (action === "approve" && reply.plan_id) monitor(reply.plan_id);
    } catch (error) {
      if (action === "approve") {
        setConfirmStep(0);
        setApprovalToken(null);
      }
      setStatus(`Hiba: ${error instanceof Error ? error.message : "ismeretlen hiba"}`);
    } finally { setBusy(false); }
  }

  function monitor(planId: string) {
    statusMonitor.current?.abort();
    const controller = new AbortController();
    statusMonitor.current = controller;
    const { signal } = controller;
    const path = `/api/v1/plans/${encodeURIComponent(planId)}/status`;
    rememberActivePlan(planId);

    let timer: number;
    let disconnected = false;
    async function poll() {
      try {
        const result = await api<{ status: string; message: string }>(path, { signal });
        if (signal.aborted) return;
        if (result.status === "completed" || result.status === "failed") {
          statusMonitor.current = null;
          rememberActivePlan(null);
          setStatus(result.message);
          logEvent(result.message);
          return;
        }
        if (disconnected) {
          disconnected = false;
          setStatus(result.message);
        }
      } catch {
        if (signal.aborted) return;
        if (!disconnected) {
          disconnected = true;
          setStatus("A végrehajtási állapot átmenetileg nem elérhető. Újrapróbálkozás…");
        }
      }
      timer = window.setTimeout(() => void poll(), 1000);
    }

    timer = window.setTimeout(() => void poll(), 1000);
    signal.addEventListener("abort", () => window.clearTimeout(timer), { once: true });
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
        <p className="muted">
          {commandsEnabled
            ? "A térképi kijelölés és a terv-ellenőrzés nem ad ki repülési parancsot. Csak az ellenőrzött tervhez tartozó, külön jóváhagyás kezdeményezhet indítást."
            : "Ground Control Room mód: a térképen kijelölhetsz célpontot és előkészítheted a koordinátákat; a SafetyGate-ellenőrzés és a jóváhagyás csak SITL konzol módban érhető el."}
        </p>
      </section>
      {envelope && <p className="envelope">Aktív korlát: max. {envelope.max_altitude_m} m magasság · {envelope.max_radius_m} m sugár · indításhoz min. {envelope.minimum_battery_percent_to_start}% akkumulátor.</p>}
      <form className="mission-form" onSubmit={review}>
        <fieldset className="mission-kind"><legend>Küldetéstípus</legend><label><input type="radio" name="mission-kind" checked={kind === "point"} onChange={() => setKind("point")} /> Pontküldetés</label><label><input type="radio" name="mission-kind" checked={kind === "waypoints"} onChange={() => { setKind("waypoints"); setAltitude("8"); }} /> Többpontos útvonal</label><label><input type="radio" name="mission-kind" checked={kind === "survey"} onChange={() => setKind("survey")} disabled={!commandsEnabled} /> Terület felderítése</label></fieldset>
        <div className="mission-map-panel">
          <ControlRoomMapPanel
            ariaLabel="Küldetési OpenStreetMap térkép"
            interactive
            geofenceRadiusM={envelope?.max_radius_m ?? null}
            maxWaypoints={MAX_MAP_WAYPOINTS}
            selectionMode={kind === "waypoints" ? "multi" : "single"}
            pendingTarget={kind === "waypoints" ? null : pendingTarget}
            plannedWaypoints={kind === "waypoints" ? mapWaypoints : []}
            onWaypointSelect={kind === "waypoints" ? undefined : applyWaypointSelection}
            onWaypointAppend={kind === "waypoints" ? appendMapWaypoint : undefined}
            onClearPending={kind === "waypoints" ? undefined : pendingTarget ? clearPendingTarget : undefined}
            onRemoveLastWaypoint={kind === "waypoints" && mapWaypoints.length ? removeLastMapWaypoint : undefined}
            onClearWaypoints={kind === "waypoints" && mapWaypoints.length ? clearMapWaypoints : undefined}
          />
          {kind === "waypoints" && (
            <ol className="map-waypoint-list" aria-label="Térképen kijelölt waypoint sorrend">
              {mapWaypoints.length === 0
                ? <li className="muted">Még nincs waypoint; kattints a térképre a sorrend felépítéséhez.</li>
                : mapWaypoints.map((waypoint, index) => (
                  <li key={`${waypoint.latitude_deg}-${waypoint.longitude_deg}-${index}`}>
                    {index + 1}. É {waypoint.north_m.toFixed(1)} m, K {waypoint.east_m.toFixed(1)} m
                    <button type="button" className="map-waypoint-remove" onClick={() => removeMapWaypointAt(index)} aria-label={`Waypoint ${index + 1} törlése`}>×</button>
                  </li>
                ))}
            </ol>
          )}
          <div className="mission-map-head">
            <p className="eyebrow">{envelope ? "KATTINTS A CÉLPONTRA" : "TÉRKÉPI SKÁLA BETÖLTÉSE"}</p>
            <fieldset className="map-range"><legend className="sr-only">Térképi látótávolság</legend>
              {MAP_RANGES_M.map((range) => <label key={range}>
                <input type="radio" name="map-range" value={range} checked={mapRange === range} onChange={() => setMapRange(range)} />
                <span>{range >= 1000 ? `${range / 1000} km` : `${range} m`}</span>
              </label>)}
            </fieldset>
          </div><svg className="mission-map" viewBox={`0 0 ${mapSize} ${mapSize}`} role="img" aria-label="Küldetési térkép" aria-disabled={!envelope} onClick={pickOnMap}>
          {mapBackground && <image href={`/api/v1/map-view?v=${encodeURIComponent(mapBackground.captured_at ?? "")}`} x={mapSize / 2 + mapBackground.centre_east_m * mapScale - mapBackground.width * mapBackground.metres_per_pixel * mapScale / 2} y={mapSize / 2 - mapBackground.centre_north_m * mapScale - mapBackground.height * mapBackground.metres_per_pixel * mapScale / 2} width={mapBackground.width * mapBackground.metres_per_pixel * mapScale} height={mapBackground.height * mapBackground.metres_per_pixel * mapScale} opacity="0.72" preserveAspectRatio="none" />}
          <line x1={mapSize / 2} y1="0" x2={mapSize / 2} y2={mapSize} className="map-axis" /><line x1="0" y1={mapSize / 2} x2={mapSize} y2={mapSize / 2} className="map-axis" />
          {envelopeRadius !== null && envelopeRadius <= mapRange && <circle cx={mapSize / 2} cy={mapSize / 2} r={envelopeRadius * mapScale} className="map-radius" />}
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
        </svg><p className="muted">
          A látótávolság a fenti kapcsolóval állítható; a nézet {mapRange >= 1000 ? `${mapRange / 1000} km` : `${mapRange} m`} sugarú.
          {envelopeRadius !== null && envelopeRadius > mapRange && ` A ${envelopeRadius >= 1000 ? `${envelopeRadius / 1000} km` : `${envelopeRadius} m`} sugárkorlát ezen a nézeten kívül esik, de érvényben van: a SafetyGate a terven ellenőrzi, nem a térképen.`}
          {envelopeRadius !== null && envelopeRadius <= mapRange && " A kör a szerver által szolgáltatott sugárkorlát."}
          {" A sárga szaggatott alakzat a geofence; a rózsaszín cellák mért akadálybizonyítékok. A kattintás csak tervez, nem repül; billentyűzettel az alábbi koordinátamezők használhatók."}
        </p></div>
        {kind !== "waypoints" && <label>Észak (m)<input type="number" value={north} onChange={(event) => setNorth(event.target.value)} /></label>}
        {kind !== "waypoints" && <label>Kelet (m)<input type="number" value={east} onChange={(event) => setEast(event.target.value)} /></label>}
        <label>Magasság (m)<input type="number" min="0" step="0.1" value={altitude} onChange={(event) => setAltitude(event.target.value)} /></label>
        {kind === "survey" && <><label>Sugár (m)<input aria-label="Sugár (m)" type="number" min="2" value={radius} onChange={(event) => setRadius(event.target.value)} /></label><label>Vonal-köz (m)<input type="number" min="1" max="15" value={spacing} onChange={(event) => setSpacing(event.target.value)} /></label></>}
        <label className="wide">Cél<input value={goal} maxLength={240} onChange={(event) => setGoal(event.target.value)} /></label>
        <button
          className="primary"
          disabled={busy || !envelope || !commandsEnabled || (kind === "waypoints" && mapWaypoints.length === 0)}
        >
          {busy ? "Ellenőrzés…" : "Terv ellenőrzése"}
        </button>
        {!commandsEnabled && <p className="muted" role="note">A terv-ellenőrzés SITL konzol módban érhető el, ha a szerveren a parancsút szándékosan engedélyezett.</p>}
      </form>
      {commandsEnabled && plan && (
        <div className="approval-panel" aria-labelledby="approval-title">
          <p className="eyebrow">SAFETYGATE-BIZONYÍTÉK · JÓVÁHAGYÁSRA VÁR</p>
          <strong id="approval-title">{confirmStep === 0 ? `Ellenőrzött terv: ${plan.summary}` : "Terv-hash megerősítése"}</strong>
          <p>{plan.goal}</p>
          {plan.plan_hash && <p><strong>Terv SHA-256:</strong> <code>{plan.plan_hash}</code></p>}
          {plan.waypoints && <p className="muted">Útvonal-bizonyíték: {plan.waypoints.length} SafetyGate által összeállított waypoint.</p>}
          <h3>Tervezett lépések</h3>
          <ol>{plan.steps.map((step, index) => <li key={`${step}-${index}`}>{step}</li>)}</ol>
          <p className="muted">
            {confirmStep === 0
              ? "Ez a terv még nem indult el. A következő lépésben a szerver rövid élettartamú jóváhagyási tokent ad ki."
              : "A jóváhagyás csak a fenti hash megerősítése után indul."}
          </p>
          <button className="primary" type="button" onClick={() => void decide("approve")} disabled={busy}>
            {confirmStep === 0 ? "Tovább a terv-hash megerősítéséhez" : "Kifejezett jóváhagyás és indítás"}
          </button>
          <button type="button" onClick={() => void decide("cancel")} disabled={busy}>Terv visszavonása</button>
        </div>
      )}
      <p className="muted" role="status">{status}</p>
      <section className="mission-events" aria-labelledby="mission-events-title"><p className="eyebrow">HELYI OPERÁTORI NAPLÓ</p><h3 id="mission-events-title">Küldetési események</h3>{events.length === 0 ? <p className="muted">Még nincs esemény ebben a böngésző-sessionben.</p> : <ol>{events.map((event, index) => <li key={`${event}-${index}`}>{event}</li>)}</ol>}</section>
    </section>
  );
}
