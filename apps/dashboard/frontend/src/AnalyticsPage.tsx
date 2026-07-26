import { useEffect, useMemo, useState } from "react";

import { api } from "./api";

type Outcome = "completed" | "failed" | "cancelled" | "rejected";
type TerminalPhase = "completed" | "failed" | "cancelled" | "rejected" | "preflight" | "takeoff";

type ReplaySummary = {
  id: string;
  recorded_at: string;
  outcome: Outcome;
  safety_decision: string;
  terminal_phase: TerminalPhase | null;
};

type VerifiedRun = ReplaySummary;
type AnalyticsState = { runs: VerifiedRun[]; excluded: number; error: string | null };

const initialState: AnalyticsState = { runs: [], excluded: 0, error: null };

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

function outcome(value: unknown): Outcome | null {
  const candidate = text(value)?.toLowerCase();
  return candidate === "completed" || candidate === "failed" || candidate === "cancelled" || candidate === "rejected" ? candidate : null;
}

function terminalPhase(value: unknown): TerminalPhase | null | undefined {
  if (value === null) return null;
  const candidate = text(value)?.toLowerCase();
  return candidate === "completed" || candidate === "failed" || candidate === "cancelled" || candidate === "rejected" || candidate === "preflight" || candidate === "takeoff" ? candidate : undefined;
}

function summary(value: unknown): ReplaySummary | null {
  if (!isRecord(value)) return null;
  const id = text(value.id);
  const recordedAt = timestamp(value.recorded_at);
  const runOutcome = outcome(value.outcome);
  const safetyDecision = text(value.safety_decision);
  const phase = terminalPhase(value.terminal_phase);
  if (!id || !recordedAt || !runOutcome || !safetyDecision || phase === undefined) return null;
  return { id, recorded_at: recordedAt, outcome: runOutcome, safety_decision: safetyDecision, terminal_phase: phase };
}

function readHistory(value: unknown): { summaries: ReplaySummary[]; excluded: number } {
  if (!isRecord(value) || !Array.isArray(value.replays)) return { summaries: [], excluded: 1 };
  const ids = new Set<string>();
  let excluded = 0;
  const summaries = value.replays.flatMap((candidate) => {
    const run = summary(candidate);
    if (!run || ids.has(run.id)) { excluded += 1; return []; }
    ids.add(run.id);
    return [run];
  });
  return { summaries, excluded };
}

function verifiesDetail(value: unknown, expected: ReplaySummary): boolean {
  const detail = summary(value);
  if (!detail || !isRecord(value) || !Array.isArray(value.events)) return false;
  if (detail.id !== expected.id || detail.recorded_at !== expected.recorded_at || detail.outcome !== expected.outcome || detail.safety_decision !== expected.safety_decision || detail.terminal_phase !== expected.terminal_phase) return false;
  return value.events.every((event) => isRecord(event) && text(event.phase) !== null && timestamp(event.timestamp) !== null);
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "ismeretlen hiba";
}

function outcomeLabel(value: Outcome): string {
  const labels: Record<Outcome, string> = {
    completed: "Sikeresen befejeződött",
    failed: "Sikertelenül zárult",
    cancelled: "Visszavonták",
    rejected: "Elutasították",
  };
  return labels[value];
}

function phaseLabel(value: TerminalPhase): string {
  const labels: Record<TerminalPhase, string> = {
    completed: "Sikeresen befejezve",
    failed: "Sikertelen lezárás",
    cancelled: "Visszavonva",
    rejected: "Elutasítva",
    preflight: "Indítás előtti ellenőrzés",
    takeoff: "Felszállás",
  };
  return labels[value];
}

function Distribution({ title, records }: { title: string; records: Array<{ key: string; label: string; count: number }> }) {
  return <section aria-labelledby={title}>
    <h3 id={title}>{title}</h3>
    {records.length === 0 ? <p className="muted">Nincs ellenőrzött adat ebben a megoszlásban.</p> : <ul className="data-list" aria-label={title}>
      {records.map((record) => <li key={record.key}><strong>{record.label}</strong><span>{record.count} futás</span></li>)}
    </ul>}
  </section>;
}

export function AnalyticsPage() {
  const [state, setState] = useState<AnalyticsState>(initialState);

  useEffect(() => {
    let active = true;
    void api<unknown>("/api/v1/missions/replays")
      .then(async (value) => {
        const history = readHistory(value);
        const details = await Promise.allSettled(history.summaries.map(async (run) => ({ run, detail: await api<unknown>(`/api/v1/missions/replays/${encodeURIComponent(run.id)}`) })));
        if (!active) return;
        let excluded = history.excluded;
        const runs = details.flatMap((result) => {
          if (result.status !== "fulfilled" || !verifiesDetail(result.value.detail, result.value.run)) { excluded += 1; return []; }
          return [result.value.run];
        });
        setState({ runs, excluded, error: null });
      })
      .catch((error: unknown) => { if (active) setState({ runs: [], excluded: 0, error: errorMessage(error) }); });
    return () => { active = false; };
  }, []);

  const outcomeDistribution = useMemo(() => {
    const totals = new Map<Outcome, number>();
    state.runs.forEach((run) => totals.set(run.outcome, (totals.get(run.outcome) ?? 0) + 1));
    return [...totals.entries()].map(([key, count]) => ({ key, label: outcomeLabel(key), count }));
  }, [state.runs]);
  const phaseDistribution = useMemo(() => {
    const totals = new Map<TerminalPhase, number>();
    state.runs.forEach((run) => { if (run.terminal_phase) totals.set(run.terminal_phase, (totals.get(run.terminal_phase) ?? 0) + 1); });
    return [...totals.entries()].map(([key, count]) => ({ key, label: phaseLabel(key), count }));
  }, [state.runs]);
  const timestamps = useMemo(() => [...state.runs].map((run) => run.recorded_at).sort(), [state.runs]);

  return <section className="content-card" aria-labelledby="analytics-title">
    <div className="section-heading"><div><p className="eyebrow">AUDIT-ANALITIKA · CSAK OLVASHATÓ</p><h2 id="analytics-title">Küldetésanalitika</h2></div></div>
    <p className="muted">Ez a nézet nem élő teljesítménymérés és nem irányítófelület. A számok csak az itt felsorolt, részletarchívummal egyező futásokból származnak.</p>
    {state.error ? <p className="muted" role="alert">Az analitikai archívum nem elérhető: {state.error}</p> : <>
      {state.excluded > 0 ? <p className="muted" role="status">{state.excluded} archívumbejegyzés nem ellenőrizhető; nem szerepel sem a számokban, sem a megoszlásokban.</p> : null}
      {state.runs.length === 0 ? <p className="muted" role="status">Nincs ellenőrzött, elemezhető küldetésarchívum.</p> : <>
        <dl className="telemetry-details" aria-label="Ellenőrzött archívum összegzése">
          <div><dt>Ellenőrzött archívum</dt><dd>{state.runs.length} ellenőrzött futás</dd></div>
          <div><dt>Első ellenőrzött futás</dt><dd><time dateTime={timestamps[0]}>{timestamps[0]}</time></dd></div>
          <div><dt>Utolsó ellenőrzött futás</dt><dd><time dateTime={timestamps.at(-1)}>{timestamps.at(-1)}</time></dd></div>
        </dl>
        <div className="live-operations-grid">
          <Distribution title="Rögzített kimenetek" records={outcomeDistribution} />
          <Distribution title="Rögzített végső fázisok" records={phaseDistribution} />
        </div>
        <section className="mission-events" aria-labelledby="analytics-boundary-title">
          <p className="eyebrow">ÉRTELMEZÉSI HATÁR</p><h3 id="analytics-boundary-title">Az archívum korlátai</h3>
          <p className="muted">A megoszlások rögzített auditkimeneteket mutatnak, nem sikerességi KPI-t, trendet vagy élő robotállapotot. A hiányzó, hibás vagy egymásnak ellentmondó archívumbejegyzések kimaradnak; ebből nem következik sem biztonság, sem ok-okozat.</p>
        </section>
      </>}
    </>}
  </section>;
}
