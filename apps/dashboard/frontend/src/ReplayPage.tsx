import { useEffect, useState } from "react";

import { api } from "./api";

type ReplaySummary = {
  id: string;
  recorded_at: string;
  outcome: string;
  safety_decision: string;
  terminal_phase: string | null;
};

type ReplayEvent = { phase: string; timestamp: string };

type ReplayDetail = ReplaySummary & {
  failure_reason: string | null;
  events: ReplayEvent[];
  preflight: {
    battery_percent: number | null;
    navigation_ready: boolean | null;
    home_position_valid: boolean | null;
    global_position_valid: boolean | null;
  };
  terminal_phase: string | null;
  telemetry: unknown[];
};

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "ismeretlen hiba";
}

function outcomeLabel(outcome: string): string {
  const labels: Record<string, string> = {
    completed: "Sikeresen befejeződött",
    failed: "Sikertelenül zárult",
    cancelled: "Visszavonták",
    rejected: "Elutasítva",
  };
  return labels[outcome.toLowerCase()] ?? outcome;
}

function safetyDecisionLabel(decision: string): string {
  const labels: Record<string, string> = {
    approved: "SafetyGate jóváhagyta",
    rejected: "SafetyGate elutasította",
    blocked: "SafetyGate blokkolta",
    "not-evaluated": "SafetyGate nem értékelte",
  };
  return labels[decision.toLowerCase()] ?? decision;
}

function phaseLabel(phase: string | null): string {
  if (phase === null || phase === "") return "nincs rögzítve";
  const labels: Record<string, string> = {
    preflight: "Indítás előtti ellenőrzés",
    takeoff: "Felszállás",
    completed: "Sikeresen befejezve",
    failed: "Sikertelenül lezárva",
  };
  return labels[phase.toLowerCase()] ?? phase;
}

function Evidence({ label, value }: { label: string; value: boolean | null }) {
  return <li><strong>{label}</strong><span>{value === null ? "nincs rögzítve" : value ? "rendben" : "nem volt rendben"}</span></li>;
}

export function ReplayPage() {
  const [runs, setRuns] = useState<ReplaySummary[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [replay, setReplay] = useState<ReplayDetail | null>(null);
  const [historyError, setHistoryError] = useState<string | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    void api<{ replays: ReplaySummary[] }>("/api/v1/missions/replays")
      .then((data) => {
        if (!active) return;
        setRuns(data.replays);
        setSelectedRunId(data.replays[0]?.id ?? null);
      })
      .catch((error: unknown) => {
        if (active) setHistoryError(errorMessage(error));
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (!selectedRunId) return;
    let active = true;
    setReplay(null);
    setDetailError(null);
    void api<ReplayDetail>(`/api/v1/missions/replays/${encodeURIComponent(selectedRunId)}`)
      .then((data) => { if (active) setReplay(data); })
      .catch((error: unknown) => { if (active) setDetailError(errorMessage(error)); });
    return () => { active = false; };
  }, [selectedRunId]);

  return <section className="content-card" aria-labelledby="replay-title">
    <div className="section-heading"><div><p className="eyebrow">IMMUTÁBILIS NAPLÓ</p><h2 id="replay-title">Küldetés-visszajátszás</h2></div></div>
    <p className="muted">Csak olvasható küldetéstörténet. A rögzített futások változatlanok; ez a nézet nem küld parancsot a robotnak.</p>

    {historyError ? <p className="muted" role="alert">A visszajátszási előzmény nem elérhető: {historyError}</p> : (
      <>
      {runs.length === 0 ? <p className="muted" role="status">Nincs elérhető, ellenőrzött küldetés-visszajátszás.</p> : <ul className="data-list" aria-label="Rögzített küldetésfutások">
          {runs.map((run) => <li key={run.id}>
            <button type="button" onClick={() => setSelectedRunId(run.id)} aria-pressed={selectedRunId === run.id} aria-label={`Visszajátszás megnyitása: ${run.id}`}>
              {run.id}
            </button>
            <small>{run.recorded_at}</small>
            <span>Kimenet: {outcomeLabel(run.outcome)}</span>
            <span>Biztonsági döntés: {safetyDecisionLabel(run.safety_decision)}</span>
          </li>)}
        </ul>}
      </>
    )}

    {detailError && <p className="muted" role="alert">A kiválasztott visszajátszás nem elérhető: {detailError}</p>}
    {replay && <section className="mission-events" aria-labelledby="selected-replay-title">
      <p className="eyebrow">BIZONYÍTÉK LÁNCA · KIVÁLASZTOTT NAPLÓ</p>
      <h3 id="selected-replay-title">Futás: {replay.id}</h3>
      <p className="muted">Rögzítve: {replay.recorded_at}</p>

      <h3>Végállapot</h3>
      <ul className="data-list">
        <li><strong>Kimenet</strong><span>{outcomeLabel(replay.outcome)}</span></li>
        <li><strong>Biztonsági döntés</strong><span>{safetyDecisionLabel(replay.safety_decision)}</span></li>
        <li><strong>Végső fázis</strong><span>{phaseLabel(replay.terminal_phase)} <small>{replay.terminal_phase ?? "nincs rögzítve"}</small></span></li>
        {replay.failure_reason !== null && replay.failure_reason !== "" && <li><strong>Hiba oka</strong><span>{replay.failure_reason}</span></li>}
      </ul>

      <h3>Indítás előtti bizonyíték</h3>
      <ul className="data-list">
        <li><strong>Akkumulátor</strong><span>{replay.preflight.battery_percent === null ? "nincs rögzítve" : `${replay.preflight.battery_percent}%`}</span></li>
        <Evidence label="Navigáció" value={replay.preflight.navigation_ready} />
        <Evidence label="Home pozíció" value={replay.preflight.home_position_valid} />
        <Evidence label="Globális pozíció" value={replay.preflight.global_position_valid} />
      </ul>

      <h3>Rögzített idővonal</h3>
      {replay.events.length === 0 ? <p className="muted">Nincs rögzített idővonalesemény.</p> : <ol>
        {replay.events.map((event, index) => <li key={`${event.phase}-${event.timestamp}-${index}`}><strong>{phaseLabel(event.phase)}</strong> <small>{event.phase}</small> · {event.timestamp}</li>)}
      </ol>}
    </section>}
  </section>;
}
