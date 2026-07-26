import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";

type Evidence = { confidence: number | null; source: string | null; observedAt: string | null };
type Claim = { category: string; statement: string; evidence: Evidence };
type OccupancyCell = { north_m: number; east_m: number; cell_size_m: number; disputed?: boolean };
type MapDocument = { cells?: OccupancyCell[]; occupancy_only?: boolean };
type MapState = { cells: OccupancyCell[]; rejectedCount: number; occupancyOnly: boolean; receivedAt: string | null };

const mapSize = 360;

function validCell(value: unknown): value is OccupancyCell {
  if (typeof value !== "object" || value === null) return false;
  const cell = value as OccupancyCell;
  return [cell.north_m, cell.east_m, cell.cell_size_m].every(Number.isFinite) && cell.cell_size_m > 0 && (cell.disputed === undefined || typeof cell.disputed === "boolean");
}

function coordinate(value: number): string { return Number.isInteger(value) ? String(value) : value.toFixed(1); }
function isRecord(value: unknown): value is Record<string, unknown> { return typeof value === "object" && value !== null; }
function validTimestamp(value: unknown): value is string { return typeof value === "string" && /^\d{4}-\d{2}-\d{2}T/.test(value) && Number.isFinite(Date.parse(value)); }
function hasCompleteEvidence(claim: Claim): boolean { return claim.evidence.source !== null && claim.evidence.confidence !== null && claim.evidence.observedAt !== null; }

function readClaim(value: unknown): Claim | null {
  if (!isRecord(value) || typeof value.category !== "string" || !value.category.trim() || typeof value.statement !== "string" || !value.statement.trim()) return null;
  const evidence = isRecord(value.evidence) ? value.evidence : {};
  return { category: value.category.trim(), statement: value.statement.trim(), evidence: {
    source: typeof evidence.source === "string" && evidence.source.trim() ? evidence.source.trim() : null,
    confidence: typeof evidence.confidence === "number" && Number.isFinite(evidence.confidence) && evidence.confidence >= 0 && evidence.confidence <= 1 ? evidence.confidence : null,
    observedAt: validTimestamp(evidence.observed_at) ? evidence.observed_at : null,
  } };
}

function readClaims(data: unknown, key: "claims" | "disputed"): Claim[] {
  if (!isRecord(data) || !Array.isArray(data[key])) return [];
  return data[key].flatMap((item) => { const claim = readClaim(item); return claim ? [claim] : []; });
}

function ClaimItem({ claim, disputed }: { claim: Claim; disputed: boolean }) {
  const { confidence, observedAt, source } = claim.evidence;
  return <li>
    <strong>{disputed ? `VITATOTT · ${claim.category}` : claim.category}</strong>
    <span>{claim.statement}</span>
    <small><span>Forrás: {source ?? "nem ellenőrizhető"}</span>{" · "}<span>Bizonyosság: {confidence === null ? "nem ellenőrizhető" : `${Math.round(confidence * 100)}%`}</span>{" · "}<span>Megfigyelve: {observedAt ? <time dateTime={observedAt}>{observedAt}</time> : "nem ellenőrizhető"}</span></small>
  </li>;
}

function ClaimSection({ claims, disputed, withheldCount }: { claims: Claim[]; disputed: boolean; withheldCount?: number }) {
  const id = disputed ? "disputed-claims-title" : "confirmed-claims-title";
  const title = disputed ? "Vitatott tudás" : "Megerősített megfigyelések";
  const summary = disputed
    ? `${claims.length} vitatott állítás. Ezek bizonytalanok, és soha nem jelentenek megerősített tényt.`
    : `${claims.length} bizonyítéklánccal rendelkező megfigyelés. A bizonyosság a forrás jelzése, nem kalibrált valószínűség.`;
  return <section aria-labelledby={id}><h3 id={id}>{title}</h3><p className="muted">{summary}</p>
    {withheldCount ? <p className="muted" role="status">{withheldCount} állítás nem jelenik meg megerősítettként, mert hiányos a forrása, bizonyossága vagy megfigyelési ideje.</p> : null}
    {claims.length ? <ul className="data-list">{claims.map((claim, index) => <ClaimItem key={`${claim.category}-${claim.statement}-${index}`} claim={claim} disputed={disputed} />)}</ul> : <p className="muted">{disputed ? "Nincs aktív vitatott állítás." : "Nincs aktív, ellenőrizhető megfigyelés."}</p>}
  </section>;
}

function OccupancyMap({ map }: { map: MapState }) {
  const span = useMemo(() => Math.max(12, ...map.cells.flatMap((cell) => [Math.abs(cell.north_m) + cell.cell_size_m / 2, Math.abs(cell.east_m) + cell.cell_size_m / 2])), [map.cells]);
  const scale = (mapSize / 2 - 20) / span;
  const canRenderCells = map.occupancyOnly;
  return <section className="world-map-panel" aria-labelledby="world-map-title">
    <p className="eyebrow">MÉRT ELFOGLALTSÁG · CSAK OLVASHATÓ</p><h3 id="world-map-title">Akadálytérkép</h3>
    <p className="muted">Forrás: világmemória foglaltsági végpont · Lekérés ideje: {map.receivedAt ? <><time dateTime={map.receivedAt}>{map.receivedAt}</time> (a cellák mérési idejét a szerződés nem közli)</> : "nem ellenőrizhető"}.</p>
    <svg className="world-map" viewBox={`0 0 ${mapSize} ${mapSize}`} role="img" aria-label="Akadály-elfoglaltsági térkép">
      <line className="map-axis" x1={mapSize / 2} y1="0" x2={mapSize / 2} y2={mapSize} /><line className="map-axis" x1="0" y1={mapSize / 2} x2={mapSize} y2={mapSize / 2} /><circle className="map-home" cx={mapSize / 2} cy={mapSize / 2} r="4" /><text className="map-label" x={mapSize / 2 + 8} y="18">É</text><text className="map-label" x="8" y={mapSize - 10}>kiindulópont</text>
      {canRenderCells && map.cells.map((cell, index) => { const side = Math.max(3, cell.cell_size_m * scale); const x = mapSize / 2 + cell.east_m * scale - side / 2; const y = mapSize / 2 - cell.north_m * scale - side / 2; const label = cell.disputed ? `Vitatott akadálymérés: É ${coordinate(cell.north_m)} m, K ${coordinate(cell.east_m)} m` : `Mért akadály: É ${coordinate(cell.north_m)} m, K ${coordinate(cell.east_m)} m`; return <rect key={`${cell.north_m}-${cell.east_m}-${index}`} className={cell.disputed ? "world-map-cell world-map-cell--disputed" : "world-map-cell"} x={x} y={y} width={side} height={side} rx="2" aria-label={label} />; })}
    </svg>
    <p className="muted">Az üres terület ismeretlen, nem szabad vagy biztonságos. {canRenderCells ? "A cellák csak mért akadályt jelentenek." : "A forrás nem igazolta, hogy ez kizárólag foglaltsági térkép; a cellák ezért rejtve maradnak."}</p>
    {map.rejectedCount ? <p className="muted" role="status">{map.rejectedCount} hibás térképcella elutasítva.</p> : null}
    <p className="world-map-legend"><span className="world-map-legend__confirmed" /> mért akadály <span className="world-map-legend__disputed" /> vitatott mérés</p>
  </section>;
}

export function WorldPage() {
  const [confirmedClaims, setConfirmedClaims] = useState<Claim[]>([]);
  const [disputedClaims, setDisputedClaims] = useState<Claim[]>([]);
  const [withheldCount, setWithheldCount] = useState(0);
  const [status, setStatus] = useState("Világmemória betöltése…");
  const [map, setMap] = useState<MapState>({ cells: [], rejectedCount: 0, occupancyOnly: false, receivedAt: null });
  const [mapStatus, setMapStatus] = useState("Akadálytérkép betöltése…");
  const refresh = useCallback(() => {
    void api<unknown>("/api/v1/world-memory").then((data) => {
      const candidates = readClaims(data, "claims");
      setConfirmedClaims(candidates.filter(hasCompleteEvidence));
      setWithheldCount(candidates.filter((claim) => !hasCompleteEvidence(claim)).length);
      setDisputedClaims(readClaims(data, "disputed"));
      setStatus("A világmemória megfigyeléseket mutat. A vitatott és a hiányos bizonyíték nem tény.");
    }).catch((error: Error) => setStatus(`A világmemória nem olvasható: ${error.message}`));
    void api<MapDocument>("/api/v1/world-map").then((data) => {
      const rawCells = Array.isArray(data.cells) ? data.cells : [];
      const cells = rawCells.filter(validCell);
      setMap({ cells, rejectedCount: rawCells.length - cells.length, occupancyOnly: data.occupancy_only === true, receivedAt: new Date().toISOString() });
      setMapStatus(data.occupancy_only === true ? "A térkép csak mért akadályokat mutat." : "A térkép foglaltsági jelentése nem igazolt; a cellák rejtve vannak.");
    }).catch(() => { setMap({ cells: [], rejectedCount: 0, occupancyOnly: false, receivedAt: null }); setMapStatus("Az akadálytérkép nem elérhető."); });
  }, []);
  useEffect(() => { refresh(); }, [refresh]);
  return <section className="content-card" aria-labelledby="world-title"><div className="section-heading"><div><p className="eyebrow">MEGOSZTOTT BIZONYÍTÉK · CSAK OLVASHATÓ</p><h2 id="world-title">Világ</h2></div><button type="button" onClick={refresh}>Adatok frissítése</button></div><p className="muted" role="status">{status}</p><OccupancyMap map={map} /><p className="muted" role="status">{mapStatus}</p><ClaimSection claims={confirmedClaims} disputed={false} withheldCount={withheldCount} /><ClaimSection claims={disputedClaims} disputed /></section>;
}
