import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "./api";

type Node = { id: string; label: string; kind: string; detail: string };
type Edge = { source: string; target: string; label: string };
type Graph = { nodes: Node[]; edges: Edge[]; rejected: number };
type Knowledge = { personal: Graph; world: Graph; boundary: string };

const graphSize = { width: 680, height: 260 };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function readGraph(value: unknown, namespace: "personal:" | "world:"): Graph {
  if (!isRecord(value) || value.namespace !== namespace || !Array.isArray(value.nodes) || !Array.isArray(value.edges)) {
    return { nodes: [], edges: [], rejected: 1 };
  }

  let rejected = 0;
  const ids = new Set<string>();
  const nodes: Node[] = [];
  for (const candidate of value.nodes) {
    if (!isRecord(candidate)) { rejected += 1; continue; }
    const id = text(candidate.id);
    const label = text(candidate.label);
    const kind = text(candidate.kind);
    const detail = typeof candidate.detail === "string" ? candidate.detail.trim() : "";
    if (!id || !id.startsWith(namespace) || !label || !kind || ids.has(id)) { rejected += 1; continue; }
    ids.add(id);
    nodes.push({ id, label, kind, detail });
  }

  const edges: Edge[] = [];
  for (const candidate of value.edges) {
    if (!isRecord(candidate)) { rejected += 1; continue; }
    const source = text(candidate.source);
    const target = text(candidate.target);
    const label = text(candidate.label);
    if (!source || !target || !label || !source.startsWith(namespace) || !target.startsWith(namespace) || !ids.has(source) || !ids.has(target)) {
      rejected += 1;
      continue;
    }
    edges.push({ source, target, label });
  }
  return { nodes, edges, rejected };
}

function readKnowledge(value: unknown): Knowledge {
  if (!isRecord(value)) return { personal: { nodes: [], edges: [], rejected: 1 }, world: { nodes: [], edges: [], rejected: 1 }, boundary: "" };
  return {
    personal: readGraph(value.personal, "personal:"),
    world: readGraph(value.world, "world:"),
    boundary: text(value.boundary) ?? "A tárolók közti határ nincs igazolva; a gráfok továbbra is külön maradnak.",
  };
}

function coordinates(graph: Graph, roots: readonly string[]) {
  const rootNodes = graph.nodes.filter((node) => roots.includes(node.kind));
  const leaves = graph.nodes.filter((node) => !roots.includes(node.kind));
  const positions = new Map<string, { x: number; y: number }>();
  const place = (nodes: Node[], x: number) => nodes.forEach((node, index) => positions.set(node.id, { x, y: graphSize.height * (index + 1) / (nodes.length + 1) }));
  place(rootNodes, 130);
  place(leaves, 510);
  return positions;
}

function GraphDrawing({ graph, roots, label }: { graph: Graph; roots: readonly string[]; label: string }) {
  const positions = useMemo(() => coordinates(graph, roots), [graph, roots]);
  if (!graph.nodes.length) return <p className="muted">Nincs biztonságosan megjeleníthető adat ebben a gráfban.</p>;
  return <svg viewBox={`0 0 ${graphSize.width} ${graphSize.height}`} role="img" aria-label={label}>
    {graph.edges.map((edge, index) => {
      const from = positions.get(edge.source);
      const to = positions.get(edge.target);
      if (!from || !to) return null;
      return <g key={`${edge.source}-${edge.target}-${index}`}>
        <line x1={from.x} y1={from.y} x2={to.x} y2={to.y} stroke="currentColor" strokeOpacity="0.55" />
        <text x={(from.x + to.x) / 2} y={(from.y + to.y) / 2 - 7} textAnchor="middle" fontSize="12">{edge.label}</text>
      </g>;
    })}
    {graph.nodes.map((node) => {
      const point = positions.get(node.id);
      if (!point) return null;
      const disputed = node.kind === "disputed";
      const root = roots.includes(node.kind);
      return <g key={node.id}>
        <circle cx={point.x} cy={point.y} r={root ? 15 : 11} className={`knowledge-node${disputed ? " knowledge-node--disputed" : root ? " knowledge-node--root" : ""}`} />
        <title>{node.detail || node.kind}</title>
        <text x={point.x + 20} y={point.y + 4} fontSize="14">{node.label}</text>
      </g>;
    })}
  </svg>;
}

function GraphPanel({ title, eyebrow, graph, roots, world }: { title: string; eyebrow: string; graph: Graph; roots: readonly string[]; world?: boolean }) {
  const id = world ? "world-knowledge-title" : "personal-knowledge-title";
  return <section aria-labelledby={id} aria-label={title}>
    <p className="eyebrow">{eyebrow}</p>
    <h3 id={id}>{title}</h3>
    <GraphDrawing graph={graph} roots={roots} label={`${title.replace(" gráf", "")} kapcsolati ábra`} />
    {graph.rejected > 0 ? <p className="muted" role="status">{graph.rejected} hibás {world ? "világ-gráf" : "személyes gráf"}adat elutasítva; kapcsolatot nem egészítettünk ki.</p> : null}
    <ul className="data-list" aria-label={`${title} csomópontjai`}>
      {graph.nodes.map((node) => <li key={node.id}>
        <strong>{world ? node.kind === "disputed" ? "Vitatott bizonyíték" : node.kind === "source" ? "Bizonyítékforrás" : "Megerősített bizonyíték" : node.kind === "person" ? "Személyes gyökér" : "Személyes állítás"}</strong>
        <span>{node.label}</span>
        <small>{node.detail || "Részlet nincs rögzítve."}</small>
      </li>)}
    </ul>
  </section>;
}

export function KnowledgePage() {
  const [knowledge, setKnowledge] = useState<Knowledge | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    void api<unknown>("/api/v1/knowledge")
      .then((data) => setKnowledge(readKnowledge(data)))
      .catch((reason: unknown) => {
        setKnowledge(null);
        setError(reason instanceof Error ? reason.message : "ismeretlen hiba");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  return <section className="content-card" aria-labelledby="knowledge-title">
    <div className="section-heading"><div><p className="eyebrow">KÉT KÜLÖN TÁROLÓ · CSAK OLVASHATÓ</p><h2 id="knowledge-title">Tudásgráf</h2></div><button type="button" onClick={refresh} disabled={loading}>{loading ? "Frissítés folyamatban…" : "Adatok frissítése"}</button></div>
    <p className="muted">Ez a nézet nem következtet kapcsolatokat és nem módosít sem személyes memóriát, sem világ-bizonyítékot.</p>
    {error ? <p className="muted" role="alert">A tudásgráf nem olvasható: {error}</p> : null}
    {loading && !knowledge ? <p className="muted" role="status">A tudásgráf betöltése folyamatban van…</p> : null}
    {knowledge ? <>
      <p className="muted" role="status">{knowledge.boundary}</p>
      <GraphPanel title="Személyes memória gráf" eyebrow="CSAK A SESSIONBEN KEZELT ÁLLÍTÁSOK" graph={knowledge.personal} roots={["person"]} />
      <GraphPanel title="Világ-bizonyíték gráf" eyebrow="MÉRT VILÁG-BIZONYÍTÉK · NEM SZEMÉLYHEZ KÖTÖTT" graph={knowledge.world} roots={["source"]} world />
    </> : null}
  </section>;
}
