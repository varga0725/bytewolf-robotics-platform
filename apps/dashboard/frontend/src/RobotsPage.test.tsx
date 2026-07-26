import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { RobotsPage } from "./RobotsPage";

afterEach(() => {
  cleanup();
  window.localStorage.clear();
  vi.unstubAllGlobals();
});

function mockRobotApi(overrides: Record<string, unknown> = {}) {
  const fresh = new Date().toISOString();
  const responses: Record<string, unknown> = {
    "/api/v1/telemetry": {
      captured_at: fresh,
      in_air: false,
      battery_percent: 76.4,
      position: { latitude_deg: 47.4979, longitude_deg: 19.0402, relative_altitude_m: 0 },
    },
    "/api/v1/safety-envelope": { max_altitude_m: 20, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    "/api/v1/cameras/front/detections": { validity: "valid", captured_at: fresh, max_age_s: 30, frame: { width: 640, height: 480 }, detections: [] },
    ...overrides,
  };
  vi.stubGlobal("fetch", vi.fn((path: string) => Promise.resolve(new Response(JSON.stringify(responses[path]), { status: responses[path] === undefined ? 404 : 200 }))));
}

describe("RobotsPage", () => {
  it("presents exactly one simulation body with only verified operational facts", async () => {
    mockRobotApi();
    render(<RobotsPage />);

    expect(await screen.findByRole("heading", { name: "Robot" })).toBeInTheDocument();
    expect(screen.getByText("SZIMULÁCIÓS BODY · 01")).toBeInTheDocument();
    expect(screen.getAllByText("KAPCSOLÓDVA").length).toBeGreaterThan(0);
    expect(screen.getByText("76,4 %")).toBeInTheDocument();
    expect(screen.getByText("47,497900, 19,040200")).toBeInTheDocument();
    expect(screen.getByText("NEM IGAZOLHATÓ", { selector: "dd" })).toBeInTheDocument();
    expect(screen.getByText(/Közvetlen repülési parancs nem adható ki ebből a nézetből/i)).toBeInTheDocument();
    expect(screen.getByText("SZOFTVERVERZIÓ NEM IGAZOLHATÓ")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /jóváhagy|indít|visszatérés|leszállás|rth/i })).not.toBeInTheDocument();
  });

  it("fails closed when telemetry and camera evidence are stale or malformed", async () => {
    mockRobotApi({
      "/api/v1/telemetry": { captured_at: "2020-01-01T00:00:00Z", in_air: true, battery_percent: 76, position: { latitude_deg: 47.4979, longitude_deg: 19.0402, relative_altitude_m: 8 } },
      "/api/v1/cameras/front/detections": { validity: "valid", captured_at: "2020-01-01T00:00:00Z", max_age_s: 1, frame: { width: 640, height: 480 }, detections: [] },
      "/api/v1/safety-envelope": { max_altitude_m: -1, max_radius_m: 50, minimum_battery_percent_to_start: 40 },
    });
    render(<RobotsPage />);

    expect((await screen.findAllByText("TELEMETRIA ELAVULT")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("NEM ELLENŐRIZHETŐ").length).toBeGreaterThan(2);
    expect(screen.getByText("KAMERA-BIZONYÍTÉK ELAVULT")).toBeInTheDocument();
    expect(screen.getByText("SAFETY MÓD NEM ELLENŐRIZHETŐ")).toBeInTheDocument();
    expect(screen.queryByText("76,0 %")).not.toBeInTheDocument();
  });
});
