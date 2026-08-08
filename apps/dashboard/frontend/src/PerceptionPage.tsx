import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "./api";

type SensorId = "front" | "down";
type Detection = { label?: unknown; confidence?: unknown; bbox?: unknown };
type ValidDetection = { label: string; confidence: number };
type Frame = { width: number; height: number; id: string | null };
type Sector = { from: number; to: number; distance: number };
type PerceptionEvidence =
  | { state: "loading" | "unavailable" | "invalid" | "stale"; reason: string; capturedAt: string | null; detections: ValidDetection[]; coverage: Sector[] }
  | { state: "fresh"; reason: string; capturedAt: string; detections: ValidDetection[]; coverage: Sector[]; frame: Frame };
type OccupancyCell = { north: number; east: number; size: number; disputed: boolean; label: string | null };
type MapState = { verified: boolean; cells: OccupancyCell[]; rejected: number };
type MapBackground = { metresPerPixel: number; width: number; height: number; capturedAt: string };

const sensors: ReadonlyArray<{ id: SensorId; label: string }> = [
  { id: "front", label: "Elülső kamera" },
  { id: "down", label: "Alsó kamera" },
];

function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null; }
function number(value: unknown): value is number { return typeof value === "number" && Number.isFinite(value); }
function time(value: unknown): value is string { return typeof value === "string" && Number.isFinite(Date.parse(value)); }
function coordinate(value: number): string { return Number.isInteger(value) ? String(value) : value.toFixed(1); }

function readFrame(value: unknown): Frame | null {
  if (!isRecord(value) || !number(value.width) || !number(value.height) || value.width <= 0 || value.height <= 0) return null;
  return { width: value.width, height: value.height, id: typeof value.frame_id === "string" && value.frame_id.trim() ? value.frame_id : null };
}

function readDetections(value: unknown, frame: Frame): ValidDetection[] | null {
  if (!Array.isArray(value)) return null;
  const detections: ValidDetection[] = [];
  for (const item of value) {
    if (!isRecord(item) || typeof item.label !== "string" || !item.label.trim() || !number(item.confidence) || item.confidence < 0 || item.confidence > 1 || !isRecord(item.bbox)) return null;
    const { x, y, width, height } = item.bbox;
    if (!number(x) || !number(y) || !number(width) || !number(height) || x < 0 || y < 0 || width <= 0 || height <= 0 || x + width > frame.width || y + height > frame.height) return null;
    detections.push({ label: item.label.trim(), confidence: item.confidence });
  }
  return detections;
}

function readCoverage(value: unknown, now: number): Sector[] {
  if (!isRecord(value) || value.validity !== "valid" || !time(value.captured_at) || !number(value.max_age_s) || value.max_age_s <= 0 || !Array.isArray(value.sectors)) return [];
  const captured = Date.parse(value.captured_at);
  if (captured > now + 1000 || now - captured > value.max_age_s * 1000) return [];
  const sectors: Sector[] = [];
  for (const item of value.sectors) {
    if (!isRecord(item) || !number(item.from_deg) || !number(item.to_deg) || !number(item.max_distance_m) || item.from_deg < -180 || item.to_deg > 180 || item.from_deg >= item.to_deg || item.max_distance_m <= 0) return [];
    sectors.push({ from: item.from_deg, to: item.to_deg, distance: item.max_distance_m });
  }
  return sectors;
}

function readEvidence(value: unknown, now = Date.now()): PerceptionEvidence {
  if (!isRecord(value) || value.validity !== "valid") return { state: "invalid", reason: "A forrás nem jelölte érvényesnek az észlelést.", capturedAt: null, detections: [], coverage: [] };
  const frame = readFrame(value.frame);
  if (!frame || !time(value.captured_at) || !number(value.max_age_s) || value.max_age_s <= 0) return { state: "invalid", reason: "Hiányos képkocka- vagy frissességi szerződés.", capturedAt: null, detections: [], coverage: [] };
  const capturedAt = value.captured_at;
  const captured = Date.parse(capturedAt);
  if (captured > now + 1000) return { state: "invalid", reason: "A bizonyíték időbélyege jövőbeli.", capturedAt, detections: [], coverage: [] };
  if (now - captured > value.max_age_s * 1000) return { state: "stale", reason: "Az észlelések és a lefedettség elavultak, ezért rejtve maradnak.", capturedAt, detections: [], coverage: [] };
  const detections = readDetections(value.detections, frame);
  if (!detections) return { state: "invalid", reason: "Legalább egy észlelés sérti a képkocka-szerződést; semmit sem mutatunk.", capturedAt, detections: [], coverage: [] };
  return { state: "fresh", reason: "Friss, szerződés szerinti észlelési bizonyíték.", capturedAt, detections, coverage: readCoverage(value.coverage, now), frame };
}

function readMap(value: unknown): MapState {
  if (!isRecord(value) || value.occupancy_only !== true || !Array.isArray(value.cells)) return { verified: false, cells: [], rejected: 0 };
  const cells: OccupancyCell[] = [];
  let rejected = 0;
  for (const item of value.cells) {
    if (!isRecord(item) || !number(item.north_m) || !number(item.east_m) || !number(item.cell_size_m) || item.cell_size_m <= 0 || (item.disputed !== undefined && typeof item.disputed !== "boolean")) { rejected += 1; continue; }
    cells.push({ north: item.north_m, east: item.east_m, size: item.cell_size_m, disputed: item.disputed === true, label: typeof item.semantic_label === "string" ? item.semantic_label : null });
  }
  return { verified: true, cells, rejected };
}

function statusLabel(state: PerceptionEvidence["state"]): string {
  return state === "fresh" ? "FRISS" : state === "stale" ? "ELAVULT" : state === "loading" ? "BETÖLTÉS" : state === "unavailable" ? "NEM ELÉRHETŐ" : "ÉRVÉNYTELEN";
}

function readMapBackground(value: unknown): MapBackground | null {
  if (!isRecord(value) || !number(value.metres_per_pixel) || value.metres_per_pixel <= 0 || !number(value.width) || !number(value.height) || !time(value.captured_at)) return null;
  return { metresPerPixel: value.metres_per_pixel, width: value.width, height: value.height, capturedAt: value.captured_at };
}

function OccupancyMap({ map, background }: { map: MapState; background: MapBackground | null }) {
  // Folded, not spread: an API-sized grid spread as arguments throws.
  const scale = useMemo(() => 125 / map.cells.reduce(
      (widest, cell) => Math.max(widest, Math.abs(cell.north) + cell.size, Math.abs(cell.east) + cell.size),
      12,
    ), [map.cells]);
  return <section className="world-map-panel" aria-labelledby="perception-map-title">
    <p className="eyebrow">KÜLÖN BIZONYÍTÉKLÁNC</p><h3 id="perception-map-title">Mért akadályok</h3>
    <svg className="world-map" viewBox="0 0 300 300" role="img" aria-label="Perception akadálytérkép">
      {background && <image className="world-map-background" href={`/api/v1/map-view?v=${encodeURIComponent(background.capturedAt)}`} x={150-background.width*background.metresPerPixel*scale/2} y={150-background.height*background.metresPerPixel*scale/2} width={background.width*background.metresPerPixel*scale} height={background.height*background.metresPerPixel*scale} preserveAspectRatio="none" />}
      <line className="map-axis" x1="150" y1="0" x2="150" y2="300" /><line className="map-axis" x1="0" y1="150" x2="300" y2="150" /><circle className="map-home" cx="150" cy="150" r="4" /><text className="map-label" x="158" y="18">É</text>
      {map.verified && map.cells.map((cell, index) => { const size = Math.max(3, cell.size * scale); const x=150+cell.east*scale, y=150-cell.north*scale; return <g key={`${cell.north}-${cell.east}-${index}`}><rect className={cell.disputed ? "world-map-cell world-map-cell--disputed" : "world-map-cell"} x={x-size/2} y={y-size/2} width={size} height={size} rx="2" aria-label={`${cell.disputed ? "Vitatott" : "Mért"} akadály${cell.label ? `, ${cell.label}` : ""}: É ${coordinate(cell.north)} m, K ${coordinate(cell.east)} m`} />{cell.label && <text className="world-map-entity-label" x={x+5} y={y-5}>{cell.label}</text>}</g>; })}
    </svg>
    <p className="muted">{map.verified ? "Az üres terület ismeretlen, nem szabad vagy biztonságos. A cellák kizárólag mért akadályt jelentenek." : "A forrás foglaltsági jelentése nem igazolt; a cellák rejtve maradnak."}</p>
    {map.rejected > 0 && <p className="muted" role="status">{map.rejected} hibás térképcella elutasítva.</p>}
  </section>;
}

export function PerceptionPage() {
  const [sensor, setSensor] = useState<SensorId>("front");
  const [evidence, setEvidence] = useState<PerceptionEvidence>({ state: "loading", reason: "Adatok betöltése…", capturedAt: null, detections: [], coverage: [] });
  const [map, setMap] = useState<MapState>({ verified: false, cells: [], rejected: 0 });
  const [background, setBackground] = useState<MapBackground | null>(null);
  // Every request carries the generation it was issued in, and only the newest
  // may answer. Without it, switching sensors while a request is in flight lets
  // the older response resolve last -- and the front camera's detections are
  // then drawn under the "Alsó kamera" heading, which is worse than showing
  // nothing: it attributes one sensor's evidence to another.
  const generation = useRef(0);
  const refresh = useCallback(() => {
    const issued = ++generation.current;
    setEvidence({ state: "loading", reason: "Adatok betöltése…", capturedAt: null, detections: [], coverage: [] });
    void Promise.allSettled([api<unknown>(`/api/v1/cameras/${sensor}/detections`), api<unknown>("/api/v1/world-map"), api<unknown>("/api/v1/map-view/meta")]).then(([detectionResult, mapResult, backgroundResult]) => {
      if (issued !== generation.current) return;
      setEvidence(detectionResult.status === "fulfilled" ? readEvidence(detectionResult.value) : { state: "unavailable", reason: "Az észlelési végpont nem elérhető.", capturedAt: null, detections: [], coverage: [] });
      setMap(mapResult.status === "fulfilled" ? readMap(mapResult.value) : { verified: false, cells: [], rejected: 0 });
      setBackground(backgroundResult.status === "fulfilled" ? readMapBackground(backgroundResult.value) : null);
    });
  }, [sensor]);

  // Re-polled for the same reason RobotsPage is: a freshness label stamped
  // once keeps claiming FRISS long past max_age_s, on a page whose stated
  // contract is that stale evidence is withheld.
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5_000);
    return () => window.clearInterval(timer);
  }, [refresh]);
  const source = sensors.find((item) => item.id === sensor) ?? sensors[0];
  return <section className="content-card" aria-labelledby="perception-title">
    <div className="section-heading"><div><p className="eyebrow">PERCEPTION · AUDITÁLHATÓ · CSAK OLVASHATÓ</p><h2 id="perception-title">Érzékelés</h2></div><button type="button" onClick={refresh}>Adatok frissítése</button></div>
    <div className="field-label"><label htmlFor="perception-sensor">Szenzorforrás</label><select id="perception-sensor" value={sensor} onChange={(event) => setSensor(event.target.value as SensorId)}>{sensors.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}</select></div>
    <p className={`telemetry-sample telemetry-sample--${evidence.state === "fresh" ? "fresh" : evidence.state === "stale" ? "stale" : "unavailable"}`} role="status">ÉSZLELÉSI FORRÁS: {statusLabel(evidence.state)}</p>
    <p className="muted">{evidence.reason}{evidence.capturedAt ? <> Rögzítve: <time dateTime={evidence.capturedAt}>{evidence.capturedAt}</time>.</> : null}</p>
    <div className="perception-grid">
      <section className="live-operations-zone perception-camera" aria-labelledby="perception-evidence-title"><p className="eyebrow">01 · KÉPI BIZONYÍTÉK</p><h3 id="perception-evidence-title">Élőkép és validált észlelések</h3>
        <img className="camera-stream-frame" src={`/api/v1/cameras/${sensor}/stream`} alt={`${source.label} élőkép`} />
        {evidence.state === "fresh" && <ul className="data-list" aria-label="Validált objektumészlelések">{evidence.detections.length ? evidence.detections.map((detection, index) => <li key={`${detection.label}-${index}`}><strong>{detection.label}</strong><span>Bizonyossági jelzés: {Math.round(detection.confidence * 100)}% (nem kalibrált valószínűség)</span></li>) : <li>Nincs észlelés az érvényes képkockában. Ez nem igazol akadálymentességet.</li>}</ul>}
        {evidence.state !== "fresh" && <p className="muted">Az objektumészlelések visszatartva maradnak, amíg nincs friss és érvényes bizonyíték.</p>}
      </section>
      <div className="perception-side-stack">
      <section className="live-operations-zone perception-coverage" aria-labelledby="perception-coverage-title"><p className="eyebrow">02 · LEFEDETTSÉGI HATÁR</p><h3 id="perception-coverage-title">Szenzorlefedettség</h3>
        {evidence.state === "fresh" && evidence.coverage.length > 0 ? <><ul className="data-list" aria-label="Igazolt szenzorlefedettség">{evidence.coverage.map((sector, index) => <li key={`${sector.from}-${sector.to}-${index}`}><strong>{sector.from}° – {sector.to}°</strong><span>Igazolt határ: legfeljebb {sector.distance} m</span></li>)}</ul><p className="muted">Az ezen kívüli terület ismeretlen; ebből a nézetből nem vezetünk le biztonságos mozgási teret.</p></> : <p className="muted"><strong>NINCS IGAZOLT LEFEDETTSÉG.</strong> Az ezen kívüli terület ismeretlen; ebből a nézetből nem vezetünk le biztonságos mozgási teret.</p>}
        <p className="muted">A képi észlelés, az akadályfoglaltság és a lefedettség külön bizonyítéklánc. Egyik sem helyettesíti a futásidejű safety shieldet.</p>
      </section>
      <section className="live-operations-zone" aria-labelledby="perception-obstacle-title"><p className="eyebrow">03 · LOKÁLIS AKADÁLYBIZONYÍTÉK</p><h3 id="perception-obstacle-title">Foglaltsági térkép</h3><OccupancyMap map={map} background={background} /></section>
      </div>
    </div>
  </section>;
}
