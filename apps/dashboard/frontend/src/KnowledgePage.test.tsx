import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { KnowledgePage } from "./KnowledgePage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

const view = {
  boundary: "A személyes memória és a világ-bizonyíték két külön tároló. Nincs köztük él: egy észlelésből soha nem lesz személyhez kötött tény.",
  personal: {
    namespace: "personal:",
    nodes: [
      { id: "personal:user", label: "Te", kind: "person", detail: "A beszélgetőpartner" },
      { id: "personal:fact:name", label: "Ferenc", kind: "name", detail: "2026-07-25" },
    ],
    edges: [{ source: "personal:user", target: "personal:fact:name", label: "így hívnak" }],
  },
  world: {
    namespace: "world:",
    nodes: [
      { id: "world:source:lidar", label: "front lidar", kind: "source", detail: "Bizonyítékforrás" },
      { id: "world:subject:crate", label: "Láda a folyosón", kind: "obstacle", detail: "A lidar akadályt mért · bizonyosság: 82%" },
      { id: "world:subject:gate", label: "Keleti átjáró", kind: "disputed", detail: "ELLENTMONDÁSOS · operátori jelentés · bizonyosság: 40%" },
    ],
    edges: [
      { source: "world:source:lidar", target: "world:subject:crate", label: "megfigyelte" },
      { source: "world:source:lidar", target: "world:subject:gate", label: "megfigyelte" },
    ],
  },
};

describe("KnowledgePage", () => {
  it("draws separate personal and world graphs with an explicit boundary and disputed evidence", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify(view), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);

    render(<KnowledgePage />);

    const personal = await screen.findByRole("region", { name: "Személyes memória gráf" });
    const world = screen.getByRole("region", { name: "Világ-bizonyíték gráf" });
    expect(personal).toHaveTextContent("Ferenc");
    expect(world).toHaveTextContent("Láda a folyosón");
    expect(world).toHaveTextContent("Vitatott bizonyíték");
    expect(world).toHaveTextContent("ELLENTMONDÁSOS");
    expect(screen.getByText(/Nincs köztük él/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Személyes memória kapcsolati ábra" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Világ-bizonyíték kapcsolati ábra" })).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledWith("/api/v1/knowledge", expect.any(Object));
  });

  it("rejects malformed and cross-boundary data instead of inventing relationships", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({
      ...view,
      personal: { ...view.personal, nodes: [{ id: "world:wrong", label: "Nem személyes", kind: "name" }], edges: [] },
      world: { ...view.world, edges: [{ source: "world:source:lidar", target: "personal:user", label: "tiltott kapcsolat" }] },
    }), { status: 200 })));

    render(<KnowledgePage />);

    expect(await screen.findByText(/hibás személyes gráfadat elutasítva/)).toBeInTheDocument();
    expect(screen.getByText(/hibás világ-gráfadat elutasítva/)).toBeInTheDocument();
    expect(screen.queryByText("Nem személyes")).not.toBeInTheDocument();
    expect(screen.queryByText("tiltott kapcsolat")).not.toBeInTheDocument();
  });

  it("reports an unavailable knowledge view without showing fabricated graph content", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: "memory offline" }), { status: 503 })));

    render(<KnowledgePage />);

    expect(await screen.findByRole("alert")).toHaveTextContent("A tudásgráf nem olvasható: memory offline");
    expect(screen.queryByRole("img", { name: "Személyes memória kapcsolati ábra" })).not.toBeInTheDocument();
  });
});
