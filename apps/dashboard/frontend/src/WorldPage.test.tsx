import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { WorldPage } from "./WorldPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

describe("WorldPage", () => {
  it("keeps confirmed observations separate from disputed knowledge and exposes evidence provenance", async () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/world-memory"
        ? {
          claims: [{
            category: "akadály",
            statement: "Raklap a folyosón",
            evidence: { source: "front lidar", confidence: 0.82, observed_at: "2026-07-25T10:30:00Z" },
          }],
          disputed: [{
            category: "útvonal",
            statement: "A keleti átjáró járható",
            evidence: { source: "operátori jelentés", confidence: 0.4, observed_at: "2026-07-25T10:31:00Z" },
          }],
        }
        : { cells: [] },
    ), { status: 200 }))));

    render(<WorldPage />);

    const confirmed = await screen.findByRole("region", { name: "Megerősített megfigyelések" });
    const disputed = screen.getByRole("region", { name: "Vitatott tudás" });
    expect(confirmed).toHaveTextContent("Raklap a folyosón");
    expect(confirmed).toHaveTextContent("Forrás: front lidar");
    expect(confirmed).toHaveTextContent("Bizonyosság: 82%");
    expect(confirmed.querySelector("time")).toHaveAttribute("dateTime", "2026-07-25T10:30:00Z");
    expect(disputed).toHaveTextContent("A keleti átjáró járható");
    expect(disputed).toHaveTextContent("soha nem jelentenek megerősített tényt");
    expect(screen.getByText(/1 bizonyítéklánccal rendelkező megfigyelés/)).toBeInTheDocument();
    expect(screen.getByText(/1 vitatott állítás/)).toBeInTheDocument();
  });

  it("withholds malformed claim evidence from confirmed observations", async () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/world-memory"
        ? {
          claims: [
            { category: "akadály", statement: "Részben ismert tárgy", evidence: { source: 7, confidence: 3, observed_at: "tegnap" } },
            { category: "", statement: "Ezt nem szabad megjeleníteni" },
          ],
          disputed: "not-an-array",
        }
        : { cells: [] },
    ), { status: 200 }))));

    render(<WorldPage />);

    const confirmed = await screen.findByRole("region", { name: "Megerősített megfigyelések" });
    expect(confirmed).not.toHaveTextContent("Részben ismert tárgy");
    expect(confirmed).toHaveTextContent("1 állítás nem jelenik meg megerősítettként");
    expect(screen.queryByText("Ezt nem szabad megjeleníteni")).not.toBeInTheDocument();
    expect(screen.queryByText("300%")).not.toBeInTheDocument();
  });

  it("renders occupancy evidence north-up and keeps disputed cells explicitly uncertain", async () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/world-memory"
        ? { claims: [], disputed: [] }
        : {
          occupancy_only: true,
          cells: [
            { north_m: 4, east_m: -2, cell_size_m: 2 },
            { north_m: -2, east_m: 3, cell_size_m: 1, disputed: true },
          ],
        },
    ), { status: 200 }))));

    render(<WorldPage />);

    expect(await screen.findByRole("img", { name: "Akadály-elfoglaltsági térkép" })).toBeInTheDocument();
    expect(screen.getByText(/Az üres terület ismeretlen/)).toBeInTheDocument();
    expect(await screen.findByLabelText("Mért akadály: É 4 m, K -2 m")).toBeInTheDocument();
    expect(screen.getByLabelText("Vitatott akadálymérés: É -2 m, K 3 m")).toBeInTheDocument();
  });

  it("withholds map cells when the source does not affirm occupancy-only semantics", async () => {
    vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
      path === "/api/v1/world-memory" ? { claims: [], disputed: [] } : {
        cells: [{ north_m: 4, east_m: 2, cell_size_m: 1 }],
      },
    ), { status: 200 }))));

    render(<WorldPage />);

    expect(await screen.findByText(/cellák ezért rejtve maradnak/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mért akadály: É 4 m, K 2 m")).not.toBeInTheDocument();
    expect(screen.getByText(/Az üres terület ismeretlen/)).toBeInTheDocument();
  });
});
