import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DeveloperPage } from "./DeveloperPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockDiagnostics(overrides: Record<string, unknown> = {}) {
  const fresh = new Date().toISOString();
  const responses: Record<string, unknown> = {
    "/api/v1/telemetry": { captured_at: fresh, in_air: false, battery_percent: 76 },
    "/api/v1/safety-envelope": { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    "/api/v1/world-map": { occupancy_only: true, cells: [{ north_m: 2, east_m: -1, cell_size_m: 1 }] },
    "/api/v1/knowledge": { boundary: "A tárolók különállók.", personal: { namespace: "personal:", nodes: [], edges: [] }, world: { namespace: "world:", nodes: [], edges: [] } },
    "/api/v1/missions/replays": { replays: [{ id: "run-17", recorded_at: fresh, outcome: "completed", safety_decision: "approved", terminal_phase: "completed" }] },
    ...overrides,
  };
  const fetchMock = vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(responses[path]), { status: responses[path] === undefined ? 404 : 200 })));
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

describe("DeveloperPage", () => {
  it("shows only validated read-only API contract availability", async () => {
    const fetchMock = mockDiagnostics();
    render(<DeveloperPage />);

    expect(await screen.findByRole("heading", { name: "Fejlesztői diagnosztika" })).toBeInTheDocument();
    expect(screen.getAllByText("SZERZŐDÉS ELLENŐRIZVE")).toHaveLength(5);
    expect(screen.getByText("Mért foglaltsági akadályok", { exact: false })).toBeInTheDocument();
    expect(screen.getAllByText("Személyes és világ-tároló különálló.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Rögzített küldetési auditok", { exact: false }).length).toBeGreaterThan(0);
    expect(screen.queryByText("run-17")).not.toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(5);
    expect(screen.queryByRole("button", { name: /indít|jóváhagy|töröl|vezérl/i })).not.toBeInTheDocument();
  });

  it("fails closed for malformed or unavailable contracts without exposing response errors", async () => {
    mockDiagnostics({
      "/api/v1/telemetry": { captured_at: "invalid", in_air: "yes", battery_percent: 76 },
      "/api/v1/safety-envelope": { max_altitude_m: -1 },
      "/api/v1/world-map": { cells: [{ north_m: 2, east_m: -1, cell_size_m: 1 }] },
      "/api/v1/knowledge": { detail: "session=private-secret" },
      "/api/v1/missions/replays": { replays: [{ id: "run-raw", recorded_at: "bad", outcome: "completed" }] },
    });
    render(<DeveloperPage />);

    expect(await screen.findAllByText("NEM ELLENŐRIZHETŐ")).toHaveLength(5);
    expect(screen.getByText(/A részleteket a szerveroldali naplókban kell vizsgálni/)).toBeInTheDocument();
    expect(screen.queryByText(/private-secret|run-raw|invalid/i)).not.toBeInTheDocument();
  });
});
