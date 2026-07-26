import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { PerceptionPage } from "./PerceptionPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockPerception(detections: unknown, worldMap: unknown) {
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(
    path.includes("detections") ? detections : worldMap,
  ), { status: 200 }))));
}

describe("PerceptionPage", () => {
  it("shows only fresh, contract-valid perception evidence with an explicit coverage boundary", async () => {
    const capturedAt = new Date().toISOString();
    mockPerception({
      validity: "valid", captured_at: capturedAt, max_age_s: 30,
      frame: { width: 640, height: 480, frame_id: "front-17" },
      coverage: { validity: "valid", captured_at: capturedAt, max_age_s: 30, frame: "front_optical", sectors: [{ from_deg: -35, to_deg: 35, max_distance_m: 12 }] },
      detections: [{ label: "jelzőbója", confidence: 0.91, bbox: { x: 10, y: 20, width: 50, height: 60 } }],
    }, { occupancy_only: true, cells: [{ north_m: 4, east_m: -2, cell_size_m: 1 }] });

    render(<PerceptionPage />);

    expect(await screen.findByText("jelzőbója")).toBeInTheDocument();
    expect(screen.getByText(/ÉSZLELÉSI FORRÁS: FRISS/i)).toBeInTheDocument();
    expect(screen.getByText(/-35° – 35°/)).toBeInTheDocument();
    expect(screen.getByLabelText("Mért akadály: É 4 m, K -2 m")).toBeInTheDocument();
    expect(screen.getByText(/Az ezen kívüli terület ismeretlen/i)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /indít|jóváhagy|repül/i })).not.toBeInTheDocument();
  });

  it("withholds all detections and coverage when evidence is stale or malformed", async () => {
    mockPerception({
      validity: "valid", captured_at: "2020-01-01T00:00:00Z", max_age_s: 1,
      frame: { width: 640, height: 480 },
      coverage: { validity: "valid", captured_at: "2020-01-01T00:00:00Z", max_age_s: 1, frame: "front", sectors: [{ from_deg: -40, to_deg: 40, max_distance_m: 8 }] },
      detections: [{ label: "régi", confidence: 0.8, bbox: { x: 1, y: 1, width: 20, height: 20 } }],
    }, { occupancy_only: true, cells: [{ north_m: 4, east_m: -2, cell_size_m: 0 }] });

    render(<PerceptionPage />);

    expect(await screen.findByText(/ÉSZLELÉSI FORRÁS: ELAVULT/i)).toBeInTheDocument();
    expect(screen.queryByText("régi")).not.toBeInTheDocument();
    expect(screen.getByText(/NINCS IGAZOLT LEFEDETTSÉG/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mért akadály: É 4 m, K -2 m")).not.toBeInTheDocument();
  });

  it("fails closed when any detection record breaches the frame contract", async () => {
    const capturedAt = new Date().toISOString();
    mockPerception({
      validity: "valid", captured_at: capturedAt, max_age_s: 30,
      frame: { width: 100, height: 100 },
      detections: [
        { label: "érvényes", confidence: 0.9, bbox: { x: 1, y: 1, width: 10, height: 10 } },
        { label: "hibás", confidence: 0.9, bbox: { x: 95, y: 1, width: 10, height: 10 } },
      ],
    }, { occupancy_only: false, cells: [{ north_m: 1, east_m: 1, cell_size_m: 1 }] });

    render(<PerceptionPage />);

    expect(await screen.findByText(/érvénytelen/i)).toBeInTheDocument();
    expect(screen.queryByText("érvényes")).not.toBeInTheDocument();
    expect(screen.queryByText("hibás")).not.toBeInTheDocument();
    expect(screen.getByText(/foglaltsági jelentése nem igazolt/i)).toBeInTheDocument();
  });
});
