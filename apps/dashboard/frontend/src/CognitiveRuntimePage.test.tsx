import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { CognitiveRuntimePage } from "./CognitiveRuntimePage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockRuntimeApi(overrides: Record<string, unknown> = {}) {
  const responses: Record<string, unknown> = {
    "/api/v1/memory": { facts: [{ id: "name", category: "name", fact: "Ferenc" }] },
    "/api/v1/world-memory": {
      claims: [{ category: "obstacle", statement: "Láda a folyosón", evidence: { source: "front lidar", confidence: 0.82, observed_at: "2026-07-26T10:00:00Z" } }],
      disputed: [{ category: "gate", statement: "Keleti átjáró", evidence: { source: "operator", confidence: 0.4, observed_at: "2026-07-26T09:59:00Z" } }],
    },
    "/api/v1/knowledge": {
      personal: { namespace: "personal:", nodes: [{ id: "personal:user", label: "Operátor", kind: "person" }], edges: [] },
      world: { namespace: "world:", nodes: [{ id: "world:source:lidar", label: "front lidar", kind: "source" }], edges: [] },
      boundary: "A két tároló külön marad.",
    },
    "/api/v1/missions/replays": {
      replays: [{ id: "run-204", recorded_at: "2026-07-26T10:15:00Z", outcome: "completed", safety_decision: "approved", terminal_phase: "completed" }],
    },
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(responses[path]), { status: responses[path] === undefined ? 404 : 200 }))));
}

describe("CognitiveRuntimePage", () => {
  it("shows only validated, auditable context, policy and outcome records without control or reasoning content", async () => {
    mockRuntimeApi();
    render(<CognitiveRuntimePage />);

    expect(await screen.findByRole("heading", { name: "Kognitív futtatókörnyezet" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Aktív kontextus" })).toHaveTextContent("1 ellenőrizhető session-emlék");
    expect(screen.getByRole("region", { name: "Policy és safety ellenőrzések" })).toHaveTextContent("SafetyGate jóváhagyta");
    expect(screen.getByRole("region", { name: "Javaslat- és kimenetaudit" })).toHaveTextContent("run-204");
    expect(screen.getByRole("region", { name: "Memória- és világreferenciák" })).toHaveTextContent("Láda a folyosón");
    expect(screen.getByText(/nem jelenít meg rejtett gondolatmenetet/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /indít|jóváhagy|leállít|végrehajt/i })).not.toBeInTheDocument();
  });

  it("withholds malformed references and never derives capabilities or proposals", async () => {
    mockRuntimeApi({
      "/api/v1/memory": { facts: [{ id: "", category: "name", fact: "hibás" }] },
      "/api/v1/world-memory": { claims: [{ category: "obstacle", statement: "forrás nélküli", evidence: {} }], disputed: "not-an-array" },
      "/api/v1/knowledge": { personal: { namespace: "world:", nodes: [], edges: [] }, world: {}, boundary: "" },
      "/api/v1/missions/replays": { replays: [{ id: "run-bad", recorded_at: "invalid", outcome: "", safety_decision: "approved" }] },
    });
    render(<CognitiveRuntimePage />);

    expect(await screen.findByText(/ellenőrizhetetlen adat elutasítva/i)).toBeInTheDocument();
    expect(screen.getByText(/Nincs ellenőrizhető javaslat vagy kimenetaudit/i)).toBeInTheDocument();
    expect(screen.getByText(/Nincs megjeleníthető képességlista/i)).toBeInTheDocument();
    expect(screen.queryByText("forrás nélküli")).not.toBeInTheDocument();
  });

  it("keeps unavailable runtime inputs explicit and empty", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "offline" }), { status: 503 })));
    render(<CognitiveRuntimePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("A Cognitive Runtime auditadatai nem elérhetők");
    expect(screen.queryByText("run-204")).not.toBeInTheDocument();
  });
});
