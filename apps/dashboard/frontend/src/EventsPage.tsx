import { useEffect, useMemo, useState } from "react";

import { api } from "./api";

type ReplaySummary = {
  id: string;
  recorded_at: string;
  outcome: string;
  safety_decision: string;
  terminal_phase: string | null;
};

type AuditEvent = { phase: string; timestamp: string };
type ReplayDetail = ReplaySummary & { failure_reason: string | null; events: AuditEvent[] };
type EventRecord = AuditEvent & { source: "küldetés-visszajátszás" };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function timestamp(value: unknown): string | null {
  const candidate = text(value);
  return candidate && /^\d{4}-\d{2}-\d{2}T/.test(candidate) && Number.isFinite(Date.parse(candidate)) ? candidate : null;
}

function readSummary(value: unknown): ReplaySummary | null {
  if (!isRecord(value)) return null;
  const id = text(value.id);
  const recordedAt = timestamp(value.recorded_at);
  const outcome = text(value.outcome);
  const safetyDecision = text(value.safety_decision);
  const terminalPhase = value.terminal_phase === null ? null : text(value.terminal_phase);
  if (!id || !recordedAt || !outcome || !safetyDecision || (value.terminal_phase !== null && !terminalPhase)) return null;
  return { id, recorded_at: recordedAt, outcome, safety_decision: safetyDecision, terminal_phase: terminalPhase };
}

function readHistory(value: unknown): { runs: ReplaySummary[]; rejected: number } {
  if (!isRecord(value) || !Array.isArray(value.replays)) return { runs: [], rejected: 1 };
  const ids = new Set<string>();
  let rejected = 0;
  const runs = value.replays.flatMap((candidate) => {
    const run = readSummary(candidate);
    if (!run || ids.has(run.id)) { rejected += 1; return []; }
    ids.add(run.id);
    return [run];
  });
  return { runs, rejected };
}

function readDetail(value: unknown, expectedId: string): { replay: ReplayDetail | null; rejected: number } {
  const summary = readSummary(value);
  if (!summary || summary.id !== expectedId || !isRecord(value) || !Array.isArray(value.events)) return { replay: null, rejected: 1 };
  let rejected = 0;
  const events = value.events.flatMap((candidate) => {
    if (!isRecord(candidate)) { rejected += 1; return []; }
    const phase = text(candidate.phase);
    const observedAt = timestamp(candidate.timestamp);
    if (!phase || !observedAt) { rejected += 1; return []; }
    return [{ phase, timestamp: observedAt }];
  });
  const failureReason = value.failure_reason === null ? null : text(value.failure_reason);
  return { replay: { ...summary, events, failure_reason: failureReason }, rejected };
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "ismeretlen hiba";
}

function severity(outcome: string): string {
  switch (outcome.toLowerCase()) {
    case "failed": return "SZÁRMAZTATOTT · KRITIKUS";
    case "rejected":
    case "cancelled": return "SZÁRMAZTATOTT · FIGYELMEZTETÉS";
    default: return "SZÁRMAZTATOTT · TÁJÉKOZTATÓ";
  }
}

function outcomeLabel(outcome: string): string {
  const labels: Record<string, string> = { completed: "befejezett", failed: "sikertelen", cancelled: "visszavont", rejected: "elutasított" };
  return labels[outcome.toLowerCase()] ?? outcome;
}

function phaseLabel(phase: string): string {
  const labels: Record<string, string> = { preflight: "Indítás előtti ellenőrzés", takeoff: "Felszállás", completed: "Befejezés", failed: "Sikertelen lezárás" };
  return labels[phase.toLowerCase()] ?? phase;
}

function EventTimeline({ replay, rejected }: { replay: ReplayDetail; rejected: number }) {
  const records: EventRecord[] = useMemo(() => replay.events.map((event) => ({ ...event, source: "küldetés-visszajátszás" })), [replay.events]);
  return <section className="mission-events" aria-labelledby="event-timeline-title">
    <p className="eyebrow">IMMUTÁBILIS AUDIT · KIVÁLASZTOTT FUTÁS</p>
    <h3 id="event-timeline-title">Futás: {replay.id}</h3>
    <p className="muted">A futás rögzítése: <time dateTime={replay.recorded_at}>{replay.recorded_at}</time>. A nézet nem módosítja a naplót és nem ad ki parancsot.</p>
    <article className="event-record" aria-label={`Futásösszegzés: ${replay.id}`}>
      <p><strong>{severity(replay.outcome)}</strong></p>
      <p><strong>Futásösszegzés · {outcomeLabel(replay.outcome)}</strong></p>
      <small><span>Időbélyeg: <time dateTime={replay.recorded_at}>{replay.recorded_at}</time></span>{" · "}<span>Forrás: küldetés-visszajátszás</span></small>
      {replay.failure_reason ? <p className="muted">Rögzített hibaok: {replay.failure_reason}</p> : null}
      <p className="muted">A súlyosság csak a futás rögzített kimenetéből származik; nem önálló szenzoros riasztás és nem élő helyzetértékelés.</p>
    </article>
    <h3>Rögzített idővonal</h3>
    {rejected > 0 ? <p className="muted" role="status">{rejected} hibás auditesemény elutasítva; ebből nem készült riasztás vagy következtetés.</p> : null}
    {records.length === 0 ? <p className="muted">Nincs megjeleníthető, validált idővonalesemény.</p> : <ol className="data-list" aria-label="Rögzített események">
      {records.map((event, index) => <li key={`${event.phase}-${event.timestamp}-${index}`}>
        <strong>{phaseLabel(event.phase)}</strong>
        <span>RÖGZÍTETT SÚLYOSSÁG NINCS</span>
        <small><span>Időbélyeg: <time dateTime={event.timestamp}>{event.timestamp}</time></span>{" · "}<span>Forrás: {event.source}</span>{" · "}<span>Következtetési határ: a fázis ténye rögzített, a kockázati szint nincs.</span></small>
      </li>)}
    </ol>}
  </section>;
}

export function EventsPage() {
  const [runs, setRuns] = useState<ReplaySummary[]>([]);
  const [historyRejected, setHistoryRejected] = useState(0);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [replay, setReplay] = useState<ReplayDetail | null>(null);
  const [detailRejected, setDetailRejected] = useState(0);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api<unknown>("/api/v1/missions/replays")
      .then((data) => {
        if (!active) return;
        const history = readHistory(data);
        setRuns(history.runs);
        setHistoryRejected(history.rejected);
        setSelectedRunId(history.runs[0]?.id ?? null);
      })
      .catch((error: unknown) => { if (active) setHistoryError(errorMessage(error)); });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedRunId) return;
    let active = true;
    setReplay(null);
    setDetailError(null);
    setDetailRejected(0);
    void api<unknown>(`/api/v1/missions/replays/${encodeURIComponent(selectedRunId)}`)
      .then((data) => {
        if (!active) return;
        const detail = readDetail(data, selectedRunId);
        if (!detail.replay) { setDetailError("A kiválasztott auditnapló szerkezete nem ellenőrizhető."); return; }
        setReplay(detail.replay);
        setDetailRejected(detail.rejected);
      })
      .catch((error: unknown) => { if (active) setDetailError(errorMessage(error)); });
    return () => { active = false; };
  }, [selectedRunId]);

  return <section className="content-card" aria-labelledby="events-title">
    <div className="section-heading"><div><p className="eyebrow">ESEMÉNYARCHÍVUM · CSAK OLVASHATÓ</p><h2 id="events-title">Események és riasztások</h2></div></div>
    <p className="muted">Ellenőrzött küldetés-visszajátszásokból olvasott auditnézet. Az élő telemetria, a feltételezett ok és a parancsvégrehajtás nem része ennek az oldalnak.</p>
    {historyError ? <p className="muted" role="alert">Az eseményarchívum nem elérhető: {historyError}</p> : <>
      {historyRejected > 0 ? <p className="muted" role="status">{historyRejected} hibás futásösszegzés elutasítva.</p> : null}
      {runs.length === 0 ? <p className="muted" role="status">Nincs elérhető, ellenőrzött eseményforrás.</p> : <ul className="data-list" aria-label="Auditnaplók">
        {runs.map((run) => <li key={run.id}>
          <button type="button" onClick={() => setSelectedRunId(run.id)} aria-pressed={selectedRunId === run.id} aria-label={`Napló megnyitása: ${run.id}`}>{run.id}</button>
          <small><time dateTime={run.recorded_at}>{run.recorded_at}</time> · rögzített kimenet: {outcomeLabel(run.outcome)}</small>
        </li>)}
      </ul>}
    </>}
    {detailError ? <p className="muted" role="alert">A kiválasztott auditnapló nem elérhető: {detailError}</p> : null}
    {replay ? <EventTimeline replay={replay} rejected={detailRejected} /> : null}
  </section>;
}
