import { useCallback, useEffect, useState } from "react";

import { api } from "./api";

type Reference = { label: string; source: string; confidence: number; observedAt: string; disputed: boolean };
type Replay = { id: string; recordedAt: string; outcome: string; safetyDecision: string; terminalPhase: string | null };
type RuntimeView = {
  factCount: number;
  references: Reference[];
  personalNodes: number;
  worldNodes: number;
  replays: Replay[];
  rejected: number;
};

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

function countFacts(value: unknown): { count: number; rejected: number } {
  if (!isRecord(value) || !Array.isArray(value.facts)) return { count: 0, rejected: 1 };
  let rejected = 0;
  const ids = new Set<string>();
  const facts = value.facts.filter((item) => {
    if (!isRecord(item)) { rejected += 1; return false; }
    const id = text(item.id);
    if (!id || !text(item.category) || !text(item.fact) || ids.has(id)) { rejected += 1; return false; }
    ids.add(id);
    return true;
  });
  return { count: facts.length, rejected };
}

function readReferences(value: unknown, key: "claims" | "disputed", disputed: boolean): { references: Reference[]; rejected: number } {
  if (!isRecord(value) || !Array.isArray(value[key])) return { references: [], rejected: 1 };
  let rejected = 0;
  const references = value[key].flatMap((item) => {
    if (!isRecord(item) || !text(item.statement) || !isRecord(item.evidence)) { rejected += 1; return []; }
    const source = text(item.evidence.source);
    const observedAt = timestamp(item.evidence.observed_at);
    const confidence = item.evidence.confidence;
    if (!source || !observedAt || typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) { rejected += 1; return []; }
    return [{ label: text(item.statement)!, source, observedAt, confidence, disputed }];
  });
  return { references, rejected };
}

function countKnowledgeNodes(value: unknown, key: "personal" | "world", namespace: string): { count: number; rejected: number } {
  if (!isRecord(value) || !isRecord(value[key]) || value[key].namespace !== namespace || !Array.isArray(value[key].nodes)) return { count: 0, rejected: 1 };
  let rejected = 0;
  const ids = new Set<string>();
  const nodes = value[key].nodes.filter((item) => {
    if (!isRecord(item)) { rejected += 1; return false; }
    const id = text(item.id);
    if (!id || !id.startsWith(namespace) || !text(item.label) || !text(item.kind) || ids.has(id)) { rejected += 1; return false; }
    ids.add(id);
    return true;
  });
  return { count: nodes.length, rejected };
}

function readReplays(value: unknown): { replays: Replay[]; rejected: number } {
  if (!isRecord(value) || !Array.isArray(value.replays)) return { replays: [], rejected: 1 };
  const ids = new Set<string>();
  let rejected = 0;
  const replays = value.replays.flatMap((item) => {
    if (!isRecord(item)) { rejected += 1; return []; }
    const id = text(item.id);
    const recordedAt = timestamp(item.recorded_at);
    const outcome = text(item.outcome);
    const safetyDecision = text(item.safety_decision);
    const terminalPhase = item.terminal_phase === null ? null : text(item.terminal_phase);
    if (!id || !recordedAt || !outcome || !safetyDecision || (item.terminal_phase !== null && !terminalPhase) || ids.has(id)) { rejected += 1; return []; }
    ids.add(id);
    return [{ id, recordedAt, outcome, safetyDecision, terminalPhase }];
  });
  return { replays, rejected };
}

function readRuntime(memory: unknown, world: unknown, knowledge: unknown, history: unknown): RuntimeView {
  const facts = countFacts(memory);
  const confirmed = readReferences(world, "claims", false);
  const disputed = readReferences(world, "disputed", true);
  const personal = countKnowledgeNodes(knowledge, "personal", "personal:");
  const worldNodes = countKnowledgeNodes(knowledge, "world", "world:");
  const replays = readReplays(history);
  return {
    factCount: facts.count,
    references: [...confirmed.references, ...disputed.references],
    personalNodes: personal.count,
    worldNodes: worldNodes.count,
    replays: replays.replays,
    rejected: facts.rejected + confirmed.rejected + disputed.rejected + personal.rejected + worldNodes.rejected + replays.rejected,
  };
}

function safetyLabel(value: string): string {
  const labels: Record<string, string> = { approved: "SafetyGate jóváhagyta", rejected: "SafetyGate elutasította", blocked: "SafetyGate blokkolta", "not-evaluated": "SafetyGate nem értékelte" };
  return labels[value.toLowerCase()] ?? value;
}

export function CognitiveRuntimePage() {
  const [view, setView] = useState<RuntimeView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    void Promise.all([api<unknown>("/api/v1/memory"), api<unknown>("/api/v1/world-memory"), api<unknown>("/api/v1/knowledge"), api<unknown>("/api/v1/missions/replays")])
      .then(([memory, world, knowledge, history]) => setView(readRuntime(memory, world, knowledge, history)))
      .catch(() => { setView(null); setError("A Cognitive Runtime auditadatai nem elérhetők; nem jelenítünk meg részleges vagy feltételezett állapotot."); })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return <section className="content-card" aria-labelledby="cognitive-runtime-title">
    <div className="section-heading"><div><p className="eyebrow">AUDITÁLHATÓ RUNTIME · CSAK OLVASHATÓ</p><h2 id="cognitive-runtime-title">Kognitív futtatókörnyezet</h2></div><button type="button" onClick={refresh} disabled={loading}>{loading ? "Frissítés folyamatban…" : "Auditadatok frissítése"}</button></div>
    <p className="muted">Ez a nézet nem jelenít meg rejtett gondolatmenetet, nem következtet képességet vagy javaslatot, és nem ad ki vezérlőparancsot.</p>
    {error ? <p className="muted" role="alert">{error}</p> : null}
    {loading && !view ? <p className="muted" role="status">Auditadatok betöltése folyamatban van…</p> : null}
    {view ? <>
      {view.rejected > 0 ? <p className="muted" role="status">{view.rejected} ellenőrizhetetlen adat elutasítva; ebből nem készült összefoglaló, referenciakapcsolat vagy auditállítás.</p> : null}
      <section aria-labelledby="runtime-context-title" aria-label="Aktív kontextus"><p className="eyebrow">AKTÍV KONTEXTUS · SZÁRMAZTATOTT ÖSSZEGZÉS</p><h3 id="runtime-context-title">Aktív kontextus</h3><ul className="data-list"><li><strong>Session-memória</strong><span>{view.factCount} ellenőrizhető session-emlék</span></li><li><strong>Világ-bizonyíték</strong><span>{view.references.filter((reference) => !reference.disputed).length} megerősített, {view.references.filter((reference) => reference.disputed).length} vitatott referencia</span></li><li><strong>Tárolóhatár</strong><span>{view.personalNodes} személyes és {view.worldNodes} világ-gráfcsomópont validálva; a két tároló nem egyesül.</span></li></ul></section>
      <section aria-labelledby="runtime-policy-title" aria-label="Policy és safety ellenőrzések"><p className="eyebrow">POLICY / SAFETY · RÖGZÍTETT AUDIT</p><h3 id="runtime-policy-title">Policy és safety ellenőrzések</h3>{view.replays.length ? <ul className="data-list">{view.replays.map((replay) => <li key={replay.id}><strong>{safetyLabel(replay.safetyDecision)}</strong><span>{replay.id} · rögzítve: <time dateTime={replay.recordedAt}>{replay.recordedAt}</time></span><small>Ez archív döntés, nem az aktuális rendszer policy-állapota.</small></li>)}</ul> : <p className="muted">Nincs ellenőrizhető, rögzített policy- vagy safety-ellenőrzés.</p>}</section>
      <section aria-labelledby="runtime-audit-title" aria-label="Javaslat- és kimenetaudit"><p className="eyebrow">JAVASLAT / KIMENET · IMMUTÁBILIS NAPLÓ</p><h3 id="runtime-audit-title">Javaslat- és kimenetaudit</h3><p className="muted">Nincs megjeleníthető képességlista: a jelenlegi GET-szerződések nem igazolnak kiválasztott képességet. Javaslatszöveget sem egészítünk ki.</p>{view.replays.length ? <ul className="data-list">{view.replays.map((replay) => <li key={replay.id}><strong>{replay.id}</strong><span>Rögzített kimenet: {replay.outcome}</span><small>Végső fázis: {replay.terminalPhase ?? "nincs rögzítve"} · forrás: küldetés-visszajátszás</small></li>)}</ul> : <p className="muted">Nincs ellenőrizhető javaslat vagy kimenetaudit.</p>}</section>
      <section aria-labelledby="runtime-references-title" aria-label="Memória- és világreferenciák"><p className="eyebrow">REFERENCIÁK · FORRÁSSAL ÉS IDŐVEL</p><h3 id="runtime-references-title">Memória- és világreferenciák</h3>{view.references.length ? <ul className="data-list">{view.references.map((reference, index) => <li key={`${reference.label}-${reference.observedAt}-${index}`}><strong>{reference.disputed ? "VITATOTT VILÁG-REFERENCIA" : "MEGERŐSÍTETT VILÁG-REFERENCIA"}</strong><span>{reference.label}</span><small>Forrás: {reference.source} · Bizonyossági jelzés: {Math.round(reference.confidence * 100)}% · Megfigyelve: <time dateTime={reference.observedAt}>{reference.observedAt}</time></small></li>)}</ul> : <p className="muted">Nincs ellenőrizhető memória- vagy világreferencia.</p>}</section>
    </> : null}
  </section>;
}
