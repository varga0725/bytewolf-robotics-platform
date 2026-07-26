import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";

type Telemetry = { capturedAt: string; inAir: boolean | null; battery: number | null; altitude: number | null; position: string | null };
type SafetyEnvelope = { maxAltitude: number; maxRadius: number; minBattery: number };
type OccupancyCell = { north: number; east: number; size: number; disputed: boolean };
type CameraEvidence = { state: "fresh" | "stale" | "unavailable"; capturedAt: string | null; detections: Array<{ label: string; confidence: number }> };
type OperationsState = {
  telemetry: Telemetry | null;
  telemetryState: "fresh" | "stale" | "unavailable";
  safety: SafetyEnvelope | null;
  cells: OccupancyCell[];
  occupancyVerified: boolean;
  camera: CameraEvidence;
  checkedAt: string | null;
};

const initialState: OperationsState = {
  telemetry: null,
  telemetryState: "unavailable",
  safety: null,
  cells: [],
  occupancyVerified: false,
  camera: { state: "unavailable", capturedAt: null, detections: [] },
  checkedAt: null,
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function validTime(value: unknown): value is string {
  return typeof value === "string" && Number.isFinite(Date.parse(value));
}

function readTelemetry(value: unknown): Telemetry | null {
  if (!isRecord(value) || !validTime(value.captured_at)) return null;
  const position = isRecord(value.position) ? value.position : null;
  const latitude = position?.latitude_deg;
  const longitude = position?.longitude_deg;
  return {
    capturedAt: value.captured_at,
    inAir: typeof value.in_air === "boolean" ? value.in_air : null,
    battery: finiteNumber(value.battery_percent) && value.battery_percent >= 0 && value.battery_percent <= 100 ? value.battery_percent : null,
    altitude: finiteNumber(position?.relative_altitude_m) ? position.relative_altitude_m : null,
    position: finiteNumber(latitude) && finiteNumber(longitude) ? `${latitude.toFixed(6)}, ${longitude.toFixed(6)}` : null,
  };
}

function telemetryState(telemetry: Telemetry | null): OperationsState["telemetryState"] {
  if (!telemetry) return "unavailable";
  return Date.now() - Date.parse(telemetry.capturedAt) > 10_000 ? "stale" : "fresh";
}

function readSafety(value: unknown): SafetyEnvelope | null {
  if (!isRecord(value)) return null;
  const maxAltitude = value.max_altitude_m;
  const maxRadius = value.max_radius_m;
  const minBattery = value.minimum_battery_percent_to_start;
  if (!finiteNumber(maxAltitude) || !finiteNumber(maxRadius) || !finiteNumber(minBattery) || maxAltitude <= 0 || maxRadius <= 0 || minBattery < 0 || minBattery > 100) return null;
  return { maxAltitude, maxRadius, minBattery };
}

function readCells(value: unknown): { verified: boolean; cells: OccupancyCell[] } {
  if (!isRecord(value) || value.occupancy_only !== true || !Array.isArray(value.cells)) return { verified: false, cells: [] };
  return {
    verified: true,
    cells: value.cells.flatMap((item) => {
      if (!isRecord(item) || !finiteNumber(item.north_m) || !finiteNumber(item.east_m) || !finiteNumber(item.cell_size_m) || item.cell_size_m <= 0 || (item.disputed !== undefined && typeof item.disputed !== "boolean")) return [];
      return [{ north: item.north_m, east: item.east_m, size: item.cell_size_m, disputed: item.disputed === true }];
    }),
  };
}

function readCamera(value: unknown): CameraEvidence {
  if (!isRecord(value) || value.validity !== "valid" || !validTime(value.captured_at) || !finiteNumber(value.max_age_s) || value.max_age_s <= 0 || !Array.isArray(value.detections)) {
    return { state: "unavailable", capturedAt: null, detections: [] };
  }
  const capturedAt = value.captured_at;
  if (Date.now() - Date.parse(capturedAt) > value.max_age_s * 1000) return { state: "stale", capturedAt, detections: [] };
  const detections = value.detections.flatMap((item) => {
    if (!isRecord(item) || typeof item.label !== "string" || !item.label.trim() || !finiteNumber(item.confidence) || item.confidence < 0 || item.confidence > 1) return [];
    return [{ label: item.label.trim(), confidence: item.confidence }];
  });
  return { state: "fresh", capturedAt, detections };
}

function localTime(value: string | null): string {
  return value ? new Date(value).toLocaleTimeString("hu-HU") : "NEM ELLENŐRIZHETŐ";
}

function metric(value: string | number | null, suffix = ""): string {
  if (value === null) return "NEM ELÉRHETŐ";
  return typeof value === "number" ? `${value.toFixed(1).replace(".", ",")}${suffix}` : value;
}

function OccupancyPreview({ verified, cells }: { verified: boolean; cells: OccupancyCell[] }) {
  const scale = useMemo(() => {
    const extent = Math.max(12, ...cells.flatMap((cell) => [Math.abs(cell.north) + cell.size, Math.abs(cell.east) + cell.size]));
    return 130 / extent;
  }, [cells]);
  return <div>
    <svg className="world-map" viewBox="0 0 300 300" role="img" aria-label="Élő akadálytérkép">
      <line className="map-axis" x1="150" y1="0" x2="150" y2="300" /><line className="map-axis" x1="0" y1="150" x2="300" y2="150" />
      <circle className="map-home" cx="150" cy="150" r="4" /><text className="map-label" x="158" y="18">É</text>
      {verified && cells.map((cell, index) => {
        const side = Math.max(3, cell.size * scale);
        return <rect key={`${cell.north}-${cell.east}-${index}`} className={cell.disputed ? "world-map-cell world-map-cell--disputed" : "world-map-cell"} x={150 + cell.east * scale - side / 2} y={150 - cell.north * scale - side / 2} width={side} height={side} rx="2" aria-label={`${cell.disputed ? "Vitatott" : "Mért"} akadály: É ${cell.north} m, K ${cell.east} m`} />;
      })}
    </svg>
    <p className="muted">{verified ? "Az üres terület ismeretlen, nem akadálymentes. A cellák kizárólag mért akadályt jelentenek." : "A forrás foglaltsági jelentése nem igazolt; a térképcellák rejtve maradnak."}</p>
  </div>;
}

export function LiveOperationsPage() {
  const [state, setState] = useState<OperationsState>(initialState);
  const refresh = useCallback(() => {
    void Promise.allSettled([
      api<unknown>("/api/v1/telemetry"),
      api<unknown>("/api/v1/safety-envelope"),
      api<unknown>("/api/v1/world-map"),
      api<unknown>("/api/v1/cameras/front/detections"),
    ]).then(([telemetryResult, safetyResult, mapResult, cameraResult]) => {
      const telemetry = telemetryResult.status === "fulfilled" ? readTelemetry(telemetryResult.value) : null;
      const map = mapResult.status === "fulfilled" ? readCells(mapResult.value) : { verified: false, cells: [] };
      setState({
        telemetry,
        telemetryState: telemetryState(telemetry),
        safety: safetyResult.status === "fulfilled" ? readSafety(safetyResult.value) : null,
        cells: map.cells,
        occupancyVerified: map.verified,
        camera: cameraResult.status === "fulfilled" ? readCamera(cameraResult.value) : { state: "unavailable", capturedAt: null, detections: [] },
        checkedAt: new Date().toISOString(),
      });
    });
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5_000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const timeline = [
    { label: "Telemetria", detail: state.telemetryState === "fresh" ? "friss minta" : state.telemetryState === "stale" ? "telemetria elavult" : "nem ellenőrizhető", time: state.telemetry?.capturedAt ?? state.checkedAt },
    { label: "Safety profil", detail: state.safety ? "szerver-konfiguráció beolvasva" : "nem ellenőrizhető", time: state.checkedAt },
    { label: "Parancsok", detail: "Nincs végrehajtási parancs ebben a csak olvasható nézetben.", time: state.checkedAt },
  ];

  return <section className="content-card live-operations" aria-labelledby="live-operations-title">
    <div className="section-heading">
      <div><p className="eyebrow">ÉLŐ HELYZETTUDATOSSÁG · CSAK OLVASHATÓ</p><h2 id="live-operations-title">Élő műveletek</h2></div>
      <button type="button" onClick={refresh}>Adatok frissítése</button>
    </div>
    <p className="muted" role="status">A nézet nem ad ki repülési, jóváhagyási vagy vészhelyzeti parancsot. Utolsó ellenőrzés: {localTime(state.checkedAt)}.</p>

    <div className="live-operations-grid">
      <section className="live-operations-zone" aria-labelledby="live-summary-title">
        <p className="eyebrow">01 · ROBOT ÉS KÜLDETÉS</p><h3 id="live-summary-title">Robot és küldetési összefoglaló</h3>
        <dl className="telemetry-details">
          <div><dt>Repülési állapot</dt><dd>{state.telemetryState === "fresh" ? state.telemetry?.inAir === true ? "LEVEGŐBEN" : state.telemetry?.inAir === false ? "FÖLDÖN" : "NEM ELÉRHETŐ" : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Akkumulátor</dt><dd>{state.telemetryState === "fresh" ? metric(state.telemetry?.battery ?? null, " %") : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Relatív magasság</dt><dd>{state.telemetryState === "fresh" ? metric(state.telemetry?.altitude ?? null, " m") : "NEM ELLENŐRIZHETŐ"}</dd></div>
          <div><dt>Aktív küldetés</dt><dd>NEM IGAZOLHATÓ</dd></div>
        </dl>
        <p className={`telemetry-sample telemetry-sample--${state.telemetryState}`}>{state.telemetryState === "fresh" ? "TELEMETRIA FRISS" : state.telemetryState === "stale" ? "TELEMETRIA ELAVULT" : "TELEMETRIA NEM ELÉRHETŐ"}</p>
        <p className="muted">A futó küldetés állapotát a jelenlegi olvasási szerződés nem közli; ezért a felület nem következtet rá telemetriából.</p>
      </section>

      <section className="live-operations-zone" aria-labelledby="live-situation-title">
        <p className="eyebrow">02 · BIZONYÍTÉK-ALAPÚ HELYZETKÉP</p><h3 id="live-situation-title">Élő helyzetkép</h3>
        <OccupancyPreview verified={state.occupancyVerified} cells={state.cells} />
        <div className="camera-stream-frame">
          <img src="/api/v1/cameras/front/stream" alt="Elülső kamera élőkép előnézet" />
          <p className="muted">{state.camera.state === "fresh" ? `Kamera-bizonyíték friss: ${state.camera.detections.length} validált észlelés.` : state.camera.state === "stale" ? "Kamera-bizonyíték elavult; az észlelések rejtve maradnak." : "A kamera-bizonyíték nem ellenőrizhető; az élőkép nem objektumértelmezés."}</p>
          {state.camera.state === "fresh" && <ul className="data-list" aria-label="Érvényes kameraészlelések">{state.camera.detections.length ? state.camera.detections.map((item, index) => <li key={`${item.label}-${index}`}><strong>{item.label}</strong><span>Bizonyossági jelzés: {Math.round(item.confidence * 100)}%</span></li>) : <li>Nincs észlelés az érvényes képkockában.</li>}</ul>}
        </div>
      </section>

      <section className="live-operations-zone" aria-labelledby="live-timeline-title">
        <p className="eyebrow">03 · ÁLLAPOT ÉS ELLENŐRZÉSI NYOM</p><h3 id="live-timeline-title">Telemetria, safety és parancsidővonal</h3>
        <dl className="telemetry-details">
          <div><dt>Max. magasság</dt><dd>{state.safety ? `${state.safety.maxAltitude} m` : "NEM ELÉRHETŐ"}</dd></div>
          <div><dt>Max. sugár</dt><dd>{state.safety ? `${state.safety.maxRadius} m` : "NEM ELÉRHETŐ"}</dd></div>
          <div><dt>Indítási akkuhatár</dt><dd>{state.safety ? `${state.safety.minBattery}%` : "NEM ELÉRHETŐ"}</dd></div>
          <div><dt>Pozíció</dt><dd>{state.telemetryState === "fresh" ? state.telemetry?.position ?? "NEM ELÉRHETŐ" : "NEM ELLENŐRIZHETŐ"}</dd></div>
        </dl>
        <ol className="mission-events" aria-label="Operátori ellenőrzési idővonal">{timeline.map((entry) => <li key={entry.label}><strong>{entry.label}</strong><span>{entry.detail}</span><small>{localTime(entry.time)}</small></li>)}</ol>
      </section>
    </div>
  </section>;
}
