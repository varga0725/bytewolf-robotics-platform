import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { LiveOperationsPage } from "./LiveOperationsPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockOperationsApi(overrides: Record<string, unknown> = {}) {
  const fresh = new Date().toISOString();
  const responses: Record<string, unknown> = {
    "/api/v1/telemetry": {
      captured_at: fresh,
      in_air: true,
      battery_percent: 76,
      position: { latitude_deg: 47.4979, longitude_deg: 19.0402, absolute_altitude_m: 120, relative_altitude_m: 8.5 },
    },
    "/api/v1/safety-envelope": { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    "/api/v1/world-map": { occupancy_only: true, cells: [{ north_m: 4, east_m: -2, cell_size_m: 1 }] },
    "/api/v1/cameras/front/detections": { validity: "valid", captured_at: fresh, max_age_s: 30, frame: { width: 640, height: 480 }, detections: [{ label: "raklap", confidence: 0.88, bbox: { x: 4, y: 5, width: 20, height: 30 } }] },
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(responses[path]), { status: responses[path] === undefined ? 404 : 200 }))));
}

describe("LiveOperationsPage", () => {
  it("presents the three read-only operator zones from verified live inputs", async () => {
    mockOperationsApi();
    render(<LiveOperationsPage />);

    expect(await screen.findByRole("heading", { name: "Élő műveletek" })).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "Robot és küldetési összefoglaló" })).toHaveTextContent("LEVEGŐBEN");
    expect(screen.getByRole("region", { name: "Élő helyzetkép" })).toHaveTextContent("raklap");
    expect(screen.getByRole("region", { name: "Telemetria, safety és parancsidővonal" })).toHaveTextContent("Nincs végrehajtási parancs");
    expect(screen.getByRole("img", { name: "Élő akadálytérkép" })).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "Elülső kamera élőkép előnézet" })).toHaveAttribute("src", "/api/v1/cameras/front/stream");
    expect(screen.queryByRole("button", { name: /jóváhagy|indít|visszatérés|leszállás/i })).not.toBeInTheDocument();
  });

  it("fails closed for stale telemetry, unverified occupancy and stale camera evidence", async () => {
    mockOperationsApi({
      "/api/v1/telemetry": { captured_at: "2020-01-01T00:00:00Z", in_air: true, battery_percent: 76, position: null },
      "/api/v1/world-map": { cells: [{ north_m: 4, east_m: -2, cell_size_m: 1 }] },
      "/api/v1/cameras/front/detections": { validity: "valid", captured_at: "2020-01-01T00:00:00Z", max_age_s: 1, frame: { width: 640, height: 480 }, detections: [{ label: "régi", confidence: 0.88, bbox: { x: 4, y: 5, width: 20, height: 30 } }] },
    });
    render(<LiveOperationsPage />);

    expect((await screen.findAllByText(/telemetria elavult/i)).length).toBeGreaterThan(0);
    expect(screen.getByText(/foglaltsági jelentése nem igazolt/i)).toBeInTheDocument();
    expect(screen.getByText(/kamera-bizonyíték elavult/i)).toBeInTheDocument();
    expect(screen.queryByLabelText("Mért akadály: É 4 m, K -2 m")).not.toBeInTheDocument();
    expect(screen.queryByText("régi")).not.toBeInTheDocument();
  });
});
