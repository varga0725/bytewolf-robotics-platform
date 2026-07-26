import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

type ContractState = "verified" | "unavailable";
type ContractCheck = { id: string; label: string; endpoint: string; state: ContractState; boundary: string };

const initialChecks: ContractCheck[] = [
  { id: "telemetry", label: "Telemetria", endpoint: "GET /api/v1/telemetry", state: "unavailable", boundary: "A friss minta és alap repülési értékek nem ellenőrizhetők." },
  { id: "safety", label: "Safety envelope", endpoint: "GET /api/v1/safety-envelope", state: "unavailable", boundary: "A futásidejű safety shieldet ez a nézet nem igazolja." },
  { id: "occupancy", label: "Világtérkép", endpoint: "GET /api/v1/world-map", state: "unavailable", boundary: "Csak explicit foglaltsági szerződésből jelenhet meg térképadat." },
  { id: "knowledge", label: "Tudásgráf", endpoint: "GET /api/v1/knowledge", state: "unavailable", boundary: "Személyes és világ-tároló különálló." },
  { id: "replays", label: "Küldetés-visszajátszások", endpoint: "GET /api/v1/missions/replays", state: "unavailable", boundary: "Csak rögzített küldetési auditok olvashatók." },
];

function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null; }
function number(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function timestamp(value: unknown): value is string { return typeof value === "string" && Number.isFinite(Date.parse(value)); }

function telemetryContract(value: unknown): boolean {
  return isRecord(value) && timestamp(value.captured_at) && typeof value.in_air === "boolean" && number(value.battery_percent) && value.battery_percent >= 0 && value.battery_percent <= 100;
}

function safetyContract(value: unknown): boolean {
  return isRecord(value) && number(value.max_altitude_m) && value.max_altitude_m > 0 && number(value.max_radius_m) && value.max_radius_m > 0 && number(value.minimum_battery_percent_to_start) && value.minimum_battery_percent_to_start >= 0 && value.minimum_battery_percent_to_start <= 100;
}

function occupancyContract(value: unknown): boolean {
  if (!isRecord(value) || value.occupancy_only !== true || !Array.isArray(value.cells)) return false;
  return value.cells.every((cell) => isRecord(cell) && number(cell.north_m) && number(cell.east_m) && number(cell.cell_size_m) && cell.cell_size_m > 0 && (cell.disputed === undefined || typeof cell.disputed === "boolean"));
}

function knowledgeContract(value: unknown): boolean {
  return isRecord(value) && typeof value.boundary === "string" && value.boundary.trim().length > 0 && isRecord(value.personal) && value.personal.namespace === "personal:" && Array.isArray(value.personal.nodes) && Array.isArray(value.personal.edges) && isRecord(value.world) && value.world.namespace === "world:" && Array.isArray(value.world.nodes) && Array.isArray(value.world.edges);
}

function replayContract(value: unknown): boolean {
  if (!isRecord(value) || !Array.isArray(value.replays)) return false;
  return value.replays.every((replay) => isRecord(replay) && typeof replay.id === "string" && replay.id.trim().length > 0 && timestamp(replay.recorded_at) && typeof replay.outcome === "string" && replay.outcome.trim().length > 0 && typeof replay.safety_decision === "string" && replay.safety_decision.trim().length > 0 && (replay.terminal_phase === null || typeof replay.terminal_phase === "string"));
}

function readChecks(values: readonly PromiseSettledResult<unknown>[]): ContractCheck[] {
  const validators = [telemetryContract, safetyContract, occupancyContract, knowledgeContract, replayContract] as const;
  return initialChecks.map((check, index) => ({ ...check, state: values[index]?.status === "fulfilled" && validators[index](values[index].value) ? "verified" : "unavailable" }));
}

export function DeveloperPage() {
  const [checks, setChecks] = useState<ContractCheck[]>(initialChecks);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    void Promise.allSettled([
      api<unknown>("/api/v1/telemetry"),
      api<unknown>("/api/v1/safety-envelope"),
      api<unknown>("/api/v1/world-map"),
      api<unknown>("/api/v1/knowledge"),
      api<unknown>("/api/v1/missions/replays"),
    ]).then((values) => setChecks(readChecks(values))).finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);
  const verified = checks.filter((check) => check.state === "verified").length;

  return <section className="content-card" aria-labelledby="developer-title">
    <div className="section-heading"><div><p className="eyebrow">DIAGNOSZTIKA · CSAK OLVASHATÓ · NINCS VEZÉRLÉS</p><h2 id="developer-title">Fejlesztői diagnosztika</h2></div><button type="button" onClick={refresh} disabled={loading}>{loading ? "Ellenőrzés folyamatban…" : "Szerződések újraellenőrzése"}</button></div>
    <p className="muted">Ez a nézet kizárólag a publikus GET-szerződések rendelkezésre állását ellenőrzi. Nem jelenít meg session-azonosítót, kulcsot, nyers válaszadatot vagy belső hibaüzenetet, és nem indít műveletet.</p>
    <p className="telemetry-sample telemetry-sample--unavailable" role="status">{loading ? "SZERZŐDÉSEK ELLENŐRZÉSE" : `${verified}/${checks.length} SZERZŐDÉS ELLENŐRIZVE`}</p>
    <div className="live-operations-grid" aria-label="API-szerződés állapotok">
      {checks.map((check) => <section className="live-operations-zone" key={check.id} aria-labelledby={`developer-${check.id}`}>
        <p className="eyebrow">{check.endpoint}</p><h3 id={`developer-${check.id}`}>{check.label}</h3>
        <strong className={check.state === "verified" ? "status-pill status-pill--ready" : "status-pill status-pill--unavailable"}>{check.state === "verified" ? "SZERZŐDÉS ELLENŐRIZVE" : "NEM ELLENŐRIZHETŐ"}</strong>
        <p className="muted">{check.state === "verified" ? check.id === "occupancy" ? "Mért foglaltsági akadályok szerződés szerint elérhetők." : check.id === "knowledge" ? "Személyes és világ-tároló különálló." : check.id === "replays" ? "Rögzített küldetési auditok szerződés szerint elérhetők." : "A szükséges publikus mezők ellenőrizhetők." : "A szerződés vagy a kapcsolat nem ellenőrizhető; ebből nem vezetünk le rendszerállapotot."}</p>
        <small>Bizonyítékhatár: {check.boundary}</small>
      </section>)}
    </div>
    <p className="muted" role="note">A részleteket a szerveroldali naplókban kell vizsgálni; a Control Roomba nem kerülhetnek hitelesítő adatok vagy belső hibaüzenetek.</p>
  </section>;
}
